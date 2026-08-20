package handlers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

const (
	RobotActionTopic = "robot/tunnel/action"
)

type zenohPublisher interface {
	Publish(keyExpr string, payload []byte) error
}

type zenohSessionPublisher struct {
	session zenoh.Session
}

func (z *zenohSessionPublisher) Publish(keyExpr string, payload []byte) error {
	ke, err := zenoh.NewKeyExpr(keyExpr)
	if err != nil {
		return err
	}
	return z.session.Put(ke, zenoh.NewZBytes(payload), nil)
}

var (
	zenohOnce      sync.Once
	zenohPub       zenohPublisher
	zenohSess      zenoh.Session
	zenohInitError error
)

func openZenoh() {
	zenohOnce.Do(func() {
		session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
		if err != nil {
			zenohInitError = err
			return
		}
		zenohSess = session
		zenohPub = &zenohSessionPublisher{session: session}
	})
}

func getZenohPublisher() (zenohPublisher, error) {
	openZenoh()
	if zenohInitError != nil {
		return nil, zenohInitError
	}

	return zenohPub, nil
}

// getZenohSession exposes the one session the tunnel opens, so the result
// subscriber and the action publisher share it rather than opening a second.
func getZenohSession() (zenoh.Session, error) {
	openZenoh()
	if zenohInitError != nil {
		return zenoh.Session{}, zenohInitError
	}
	return zenohSess, nil
}

func PublishRobotAction(payload []byte) error {
	pub, err := getZenohPublisher()
	if err != nil {
		return err
	}
	return pub.Publish(RobotActionTopic, payload)
}

type Handlers struct {
	Logger *zap.Logger

	// Identity and pricing this tunnel publishes on the discovery endpoints.
	RobotID          string
	ProfileID        string
	Network          string
	PayTo            string
	SkillCatalogPath string

	// Execution results recorded from Zenoh, keyed by action_id.
	Statuses  *statusStore
	resultSub *zenoh.Subscriber

	// Publisher is the transport used to reach the robot. Left nil in
	// production, where the process-wide Zenoh session is used; set in tests so
	// the settlement-gating contract can be exercised without a live session.
	Publisher zenohPublisher
}

func NewHandlers(logger *zap.Logger) *Handlers {
	return &Handlers{
		Logger:   logger,
		Statuses: sharedStatuses,
	}
}

func (h *Handlers) PostAction(c *gin.Context) {
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "failed to read request body"})
		return
	}

	if len(body) > 0 && !json.Valid(body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "request body must be valid JSON"})
		return
	}

	var payload interface{}
	if len(body) > 0 {
		if err := json.Unmarshal(body, &payload); err != nil {
			payload = string(body)
		}
	}

	var paymentPayload interface{}
	if value, ok := c.Get("x402_payload"); ok {
		paymentPayload = value
	}

	var paymentRequirements interface{}
	if value, ok := c.Get("x402_requirements"); ok {
		paymentRequirements = value
	}

	event := gin.H{
		"payload": payload,
		"transaction_details": gin.H{
			"payment_payload":      paymentPayload,
			"payment_requirements": paymentRequirements,
		},
		"timestamp": time.Now().Format(time.RFC3339),
	}

	eventBytes, err := json.Marshal(event)
	if err != nil {
		h.Logger.Warn("failed to marshal action event", zap.Error(err))
		c.JSON(http.StatusInternalServerError, gin.H{
			"error": "failed to encode action event",
		})
		return
	}

	actionID, budget := actionIdentity(body)

	// Refused before anything is published. An action with no correlation id has
	// an outcome nobody can observe, so it could never be settled safely — and a
	// request that will be refused must not reach the robot at all. Checking
	// after publishing would put it on the wire and only then say no.
	if actionID == "" || h.Statuses == nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"error": "action_id is required to correlate the robot's result",
		})
		return
	}

	// Register interest before publishing. Registering afterwards is a race the
	// simulator wins whenever it answers quickly, and losing it would look like
	// a timeout.
	done := h.Statuses.subscribe(actionID)

	pub := h.Publisher
	if pub == nil {
		pub, err = getZenohPublisher()
		if err != nil {
			h.Logger.Warn("failed to initialize zenoh publisher", zap.Error(err))
			c.JSON(http.StatusBadGateway, gin.H{"error": "robot transport unavailable"})
			return
		}
	}
	if err := pub.Publish(RobotActionTopic, eventBytes); err != nil {
		h.Logger.Warn("failed to publish action event", zap.Error(err))
		c.JSON(http.StatusBadGateway, gin.H{"error": "failed to reach the robot"})
		return
	}

	// Accepted, not finished. The robot runs asynchronously and the terminal
	// outcome is read back from GET /action/{action_id}/status, correlated by
	// this id. Settlement is deliberately not part of this response: the watcher
	// below runs it only if the simulator reports success, so a failed or
	// timed-out episode leaves the authorization signed and unspent.
	var settle SettleFunc
	if value, ok := c.Get("x402_settle"); ok {
		if fn, ok := value.(SettleFunc); ok {
			settle = fn
		}
	}
	go h.watchExecution(actionID, done, executionTimeout(budget), settle)

	c.JSON(http.StatusAccepted, gin.H{
		"status":     "accepted",
		"action_id":  actionID,
		"robot_id":   h.RobotID,
		"status_url": "/action/" + actionID + "/status",
		"timestamp":  time.Now().Format(time.RFC3339),
	})
}

// watchExecution waits for the correlated result and decides, once, whether the
// payment is settled. It is the whole of the no-settle-on-failure guarantee:
// nothing else in this tunnel can move money.
func (h *Handlers) watchExecution(actionID string, done <-chan ActionStatus,
	timeout time.Duration, settle SettleFunc) {
	status, known := awaitResult(done, timeout)

	if !known {
		h.Statuses.put(ActionStatus{
			ActionID:  actionID,
			RobotID:   h.RobotID,
			State:     stateTimeout,
			UpdatedAt: time.Now().UTC().Format(time.RFC3339),
		})
		h.Logger.Warn("no result before the deadline; not settling",
			zap.String("action_id", actionID))
		return
	}
	if status.State != stateSucceeded {
		h.Logger.Info("execution did not succeed; not settling",
			zap.String("action_id", actionID), zap.String("state", status.State))
		return
	}
	if settle == nil {
		h.Logger.Warn("no settlement callback for a successful action",
			zap.String("action_id", actionID))
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), settlementTimeout)
	defer cancel()
	record, err := settle(ctx)
	if err != nil {
		h.Logger.Warn("settlement failed after a successful action",
			zap.String("action_id", actionID), zap.Error(err))
		h.Statuses.settled(actionID, nil, err.Error())
		return
	}
	h.Logger.Info("settled after success",
		zap.String("action_id", actionID), zap.String("tx", record.Transaction))
	h.Statuses.settled(actionID, record, "")
}
