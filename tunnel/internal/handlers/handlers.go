package handlers

import (
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
		Statuses: newStatusStore(),
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

	// Register interest before publishing. Registering afterwards is a race the
	// simulator wins whenever it answers quickly, and losing it would look like
	// a timeout.
	var (
		status ActionStatus
		known  bool
		done   <-chan ActionStatus
	)
	if actionID != "" && h.Statuses != nil {
		done = h.Statuses.subscribe(actionID)
	}

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

	// The x402 middleware settles after this handler returns, and only when the
	// response is not an error. Answering "accepted" before the robot has run
	// would therefore settle a payment for work that may still fail — which is
	// exactly what a paid action must not do. So this waits for the robot's own
	// answer and reports a failure as a failure.
	if done == nil {
		c.JSON(http.StatusBadRequest, gin.H{
			"error": "action_id is required to correlate the robot's result",
		})
		return
	}
	status, known = awaitResult(done, executionTimeout(budget))

	if !known {
		c.JSON(http.StatusGatewayTimeout, gin.H{
			"action_id": actionID,
			"state":     "timeout",
			"error":     "the robot did not answer before the deadline",
		})
		return
	}
	if status.State != stateSucceeded {
		c.JSON(http.StatusBadGateway, gin.H{
			"action_id": actionID,
			"state":     status.State,
			"result":    status.Result,
			"error":     "the robot did not complete the action",
		})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"status":    "succeeded",
		"action_id": actionID,
		"robot_id":  status.RobotID,
		"skill_id":  status.SkillID,
		"result":    status.Result,
		"timestamp": time.Now().Format(time.RFC3339),
	})
}
