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
	"encoding/json"
	"net/http"
	"os"
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
)

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
	ActionID       string          `json:"action_id"`
	RobotID        string          `json:"robot_id,omitempty"`
	SkillID        string          `json:"skill_id,omitempty"`
	State          string          `json:"state"`
	ParamsHash     string          `json:"params_hash,omitempty"`
	IdempotencyKey string          `json:"idempotency_key,omitempty"`
	ProfileID      string          `json:"profile_id,omitempty"`
	Result         json.RawMessage `json:"result,omitempty"`
	UpdatedAt      string          `json:"updated_at"`
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
}

func newStatusStore() *statusStore {
	return &statusStore{entries: make(map[string]ActionStatus)}
}

func (s *statusStore) put(status ActionStatus) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.entries[status.ActionID] = status
}

func (s *statusStore) get(actionID string) (ActionStatus, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	status, ok := s.entries[actionID]
	return status, ok
}

var resultSubOnce sync.Once

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
			h.Statuses.put(ActionStatus{
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
