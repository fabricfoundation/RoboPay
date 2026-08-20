package handlers

// Minimal tunnel integration required to expose a robot profile and to
// correlate asynchronous execution results through the relay.
//
// POST /action already publishes a paid action onto Zenoh, but a caller has no
// way to ask what robot is on the other end, what it can do, what that costs,
// or how a submitted action ended. Those three questions are what the relay
// needs answered to complete a paid action, so this file adds exactly three
// read-only endpoints and nothing else:
//
//	GET /robot                     what robot is connected
//	GET /skills                    what it can do and what each skill costs
//	GET /action/:action_id/status  how a submitted action ended
//
// The status is not synthesised here. The tunnel subscribes to the same
// robot/tunnel/result topic the simulator publishes on and stores what it
// receives, keyed by action_id. An action nobody has answered for is reported
// as pending; an action that failed is reported as failed. Reporting anything
// else would make the endpoint a decoration rather than a status.

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

const (
	// RobotResultTopic carries the simulator's answer to a published action.
	RobotResultTopic = "robot/tunnel/result"

	statePending   = "pending"
	stateSucceeded = "succeeded"
	stateFailed    = "failed"
	stateTimeout   = "timeout"
)

// : How long a settlement call may take before it is abandoned. A settlement
// : that never returns must not hold a goroutine open for ever.
const settlementTimeout = 90 * time.Second

// SettleFunc settles an already-verified payment. The payment gate injects it
// into the request context; the action handler calls it only after the
// simulator reports success, which is what keeps a failed action unpaid.
type SettleFunc func(ctx context.Context) (*SettlementRecord, error)

// SettlementRecord is what a completed settlement leaves behind.
type SettlementRecord struct {
	Transaction string `json:"transaction"`
	Network     string `json:"network"`
	Payer       string `json:"payer"`
}

// Skill is one entry of the robot's published catalogue. The catalogue is the
// profile's own skill-catalog.json — the same file the registry publishes — so
// discovery cannot drift from what the profile declares.
type Skill struct {
	SkillID         string          `json:"skill_id"`
	Description     string          `json:"description"`
	PaymentRequired bool            `json:"payment_required"`
	PriceUSDC       string          `json:"price_usdc"`
	Params          json.RawMessage `json:"params,omitempty"`
}

// ActionStatus is what a caller gets back for one submitted action.
type ActionStatus struct {
	ActionID        string            `json:"action_id"`
	RobotID         string            `json:"robot_id,omitempty"`
	SkillID         string            `json:"skill_id,omitempty"`
	State           string            `json:"state"`
	ParamsHash      string            `json:"params_hash,omitempty"`
	IdempotencyKey  string            `json:"idempotency_key,omitempty"`
	ProfileID       string            `json:"profile_id,omitempty"`
	Result          json.RawMessage   `json:"result,omitempty"`
	Settled         bool              `json:"settled"`
	Settlement      *SettlementRecord `json:"settlement,omitempty"`
	SettlementError string            `json:"settlement_error,omitempty"`
	UpdatedAt       string            `json:"updated_at"`
}

// resultEnvelope is the shape the simulator bridge publishes.
type resultEnvelope struct {
	ActionID       string          `json:"action_id"`
	RobotID        string          `json:"robot_id"`
	SkillID        string          `json:"skill_id"`
	ParamsHash     string          `json:"params_hash"`
	IdempotencyKey string          `json:"idempotency_key"`
	ProfileID      string          `json:"profile_id"`
	Status         string          `json:"status"`
	Result         json.RawMessage `json:"result"`
}

type statusStore struct {
	mu      sync.RWMutex
	entries map[string]ActionStatus
	waiters map[string][]chan ActionStatus
}

func newStatusStore() *statusStore {
	return &statusStore{
		entries: make(map[string]ActionStatus),
		waiters: make(map[string][]chan ActionStatus),
	}
}

func (s *statusStore) put(status ActionStatus) {
	s.mu.Lock()
	s.entries[status.ActionID] = status
	waiting := s.waiters[status.ActionID]
	delete(s.waiters, status.ActionID)
	s.mu.Unlock()
	// Buffered by one, so a waiter that has already timed out cannot block the
	// subscriber callback.
	for _, ch := range waiting {
		ch <- status
		close(ch)
	}
}

// settled records the outcome of the settlement attempt against an action that
// already has a result, so the status endpoint can report both halves.
func (s *statusStore) settled(actionID string, record *SettlementRecord, failure string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	status, ok := s.entries[actionID]
	if !ok {
		status = ActionStatus{ActionID: actionID}
	}
	status.Settled = record != nil
	status.Settlement = record
	status.SettlementError = failure
	status.UpdatedAt = time.Now().UTC().Format(time.RFC3339)
	s.entries[actionID] = status
}

func (s *statusStore) get(actionID string) (ActionStatus, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	status, ok := s.entries[actionID]
	return status, ok
}

// subscribe registers interest in an action's result before it is published,
// and returns a channel that receives the answer exactly once. Registering
// after publishing is a race the simulator wins whenever it answers quickly,
// and losing that race is indistinguishable from a timeout.
func (s *statusStore) subscribe(actionID string) <-chan ActionStatus {
	ch := make(chan ActionStatus, 1)
	s.mu.Lock()
	defer s.mu.Unlock()
	if existing, ok := s.entries[actionID]; ok {
		ch <- existing
		close(ch)
		return ch
	}
	s.waiters[actionID] = append(s.waiters[actionID], ch)
	return ch
}

// awaitResult waits for a subscribed answer, or gives up. The boolean
// distinguishes "the robot said it failed" from "the robot never answered":
// both refuse settlement, but only one of them is an execution result.
func awaitResult(done <-chan ActionStatus, timeout time.Duration) (ActionStatus, bool) {
	select {
	case status, ok := <-done:
		return status, ok
	case <-time.After(timeout):
		return ActionStatus{}, false
	}
}

// actionIdentity reads the identity fields and the episode budget out of the
// request body. Both spellings are accepted because the relay forwards the
// caller's body verbatim.
type actionIdentityFields struct {
	ActionID       string
	RobotID        string
	SkillID        string
	IdempotencyKey string
	BudgetSeconds  float64
}

// missing names the first identity field the request left out. The simulator
// bridge refuses an envelope without all four, so publishing one only puts a
// message on the wire that is going to be rejected at the other end — and an
// invalid request is supposed to reach neither Zenoh nor the robot.
func (f actionIdentityFields) missing() string {
	for _, field := range []struct {
		name  string
		value string
	}{
		{"action_id", f.ActionID},
		{"robot_id", f.RobotID},
		{"skill_id", f.SkillID},
		{"idempotency_key", f.IdempotencyKey},
	} {
		if field.value == "" {
			return field.name
		}
	}
	return ""
}

func actionIdentity(body []byte) actionIdentityFields {
	var envelope struct {
		ActionID         string `json:"action_id"`
		ActionIDCamel    string `json:"actionId"`
		RobotID          string `json:"robot_id"`
		RobotIDCamel     string `json:"robotId"`
		SkillID          string `json:"skill_id"`
		SkillIDCamel     string `json:"skillId"`
		IdempotencyKey   string `json:"idempotency_key"`
		IdempotencyCamel string `json:"idempotencyKey"`
		Params           struct {
			MaxDurationSec float64 `json:"maxDurationSec"`
		} `json:"params"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil {
		return actionIdentityFields{}
	}
	pick := func(a, b string) string {
		if a != "" {
			return a
		}
		return b
	}
	return actionIdentityFields{
		ActionID:       pick(envelope.ActionID, envelope.ActionIDCamel),
		RobotID:        pick(envelope.RobotID, envelope.RobotIDCamel),
		SkillID:        pick(envelope.SkillID, envelope.SkillIDCamel),
		IdempotencyKey: pick(envelope.IdempotencyKey, envelope.IdempotencyCamel),
		BudgetSeconds:  envelope.Params.MaxDurationSec,
	}
}

// executionTimeout bounds the wait by what the caller asked the robot to spend,
// plus room for start-up and the answer coming back. ACTION_TIMEOUT_SECONDS
// overrides it for a deployment whose robot is slower than this one.
func executionTimeout(budgetSeconds float64) time.Duration {
	if raw := os.Getenv("ACTION_TIMEOUT_SECONDS"); raw != "" {
		if seconds, err := strconv.ParseFloat(raw, 64); err == nil && seconds > 0 {
			return time.Duration(seconds * float64(time.Second))
		}
	}
	if budgetSeconds <= 0 {
		budgetSeconds = 60
	}
	return time.Duration((budgetSeconds + 45) * float64(time.Second))
}

var (
	resultSubOnce sync.Once
	// The subscription is declared once for the process, so the results it
	// records have to outlive any single Handlers value. setupRouter builds a
	// fresh Handlers on every config-driven restart, and a per-instance store
	// would leave the new one deaf: the subscriber would keep writing to the
	// old store while the status endpoint read an empty new one, reporting
	// every action as pending for ever. Tests override Statuses for isolation.
	sharedStatuses = newStatusStore()
)

// StartResultSubscriber begins recording simulator results so that
// GET /action/:action_id/status can answer from real execution rather than
// from an assumption. It reuses the session the publisher already opened, and
// declares the subscription once per process because setupRouter runs again on
// every config-driven restart.
func (h *Handlers) StartResultSubscriber() error {
	var err error
	resultSubOnce.Do(func() { err = h.declareResultSubscriber() })
	return err
}

func (h *Handlers) declareResultSubscriber() error {
	session, err := getZenohSession()
	if err != nil {
		return err
	}
	ke, err := zenoh.NewKeyExpr(RobotResultTopic)
	if err != nil {
		return err
	}
	sub, err := session.DeclareSubscriber(ke, zenoh.Closure[zenoh.Sample]{
		Call: func(sample zenoh.Sample) {
			var envelope resultEnvelope
			if err := json.Unmarshal(sample.Payload().Bytes(), &envelope); err != nil {
				h.Logger.Warn("unparseable result envelope", zap.Error(err))
				return
			}
			if envelope.ActionID == "" {
				return
			}
			state := stateFailed
			if envelope.Status == "success" {
				state = stateSucceeded
			}
			sharedStatuses.put(ActionStatus{
				ActionID:       envelope.ActionID,
				RobotID:        envelope.RobotID,
				SkillID:        envelope.SkillID,
				State:          state,
				ParamsHash:     envelope.ParamsHash,
				IdempotencyKey: envelope.IdempotencyKey,
				ProfileID:      envelope.ProfileID,
				Result:         envelope.Result,
				UpdatedAt:      time.Now().UTC().Format(time.RFC3339),
			})
			h.Logger.Info("recorded action result",
				zap.String("action_id", envelope.ActionID),
				zap.String("state", state))
		},
	}, nil)
	if err != nil {
		return err
	}
	h.resultSub = &sub
	return nil
}

// GetRobotProfile answers "what robot is on the other end of this tunnel".
func (h *Handlers) GetRobotProfile(c *gin.Context) {
	skills, err := h.loadSkills()
	if err != nil {
		h.Logger.Warn("skill catalogue unavailable", zap.Error(err))
	}
	ids := make([]string, 0, len(skills))
	for _, skill := range skills {
		ids = append(ids, skill.SkillID)
	}
	c.JSON(http.StatusOK, gin.H{
		"robot_id":   h.RobotID,
		"profile_id": h.ProfileID,
		"network":    h.Network,
		"pay_to":     h.PayTo,
		"skills":     ids,
	})
}

// GetSkills answers "what can it do, and what does each skill cost".
func (h *Handlers) GetSkills(c *gin.Context) {
	skills, err := h.loadSkills()
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"error": "skill catalogue unavailable", "detail": err.Error(),
		})
		return
	}
	c.JSON(http.StatusOK, gin.H{"robot_id": h.RobotID, "skills": skills})
}

// GetActionStatus answers "how did that action end", from the recorded result.
func (h *Handlers) GetActionStatus(c *gin.Context) {
	actionID := c.Param("action_id")
	if actionID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "action_id is required"})
		return
	}
	if status, ok := h.Statuses.get(actionID); ok {
		c.JSON(http.StatusOK, status)
		return
	}
	// Not knowing yet is a real answer, and a different one from failure.
	c.JSON(http.StatusOK, ActionStatus{
		ActionID:  actionID,
		RobotID:   h.RobotID,
		State:     statePending,
		UpdatedAt: time.Now().UTC().Format(time.RFC3339),
	})
}

// loadSkills reads the profile's own catalogue. SKILL_CATALOG_PATH points at
// it; without the file the tunnel says so rather than inventing a catalogue.
func (h *Handlers) loadSkills() ([]Skill, error) {
	path := h.SkillCatalogPath
	if path == "" {
		path = os.Getenv("SKILL_CATALOG_PATH")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var skills []Skill
	if err := json.Unmarshal(raw, &skills); err != nil {
		return nil, err
	}
	return skills, nil
}
