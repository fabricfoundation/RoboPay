package handlers

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
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
	zenohInitError error
)

func getZenohPublisher() (zenohPublisher, error) {
	zenohOnce.Do(func() {
		session, err := zenoh.Open(zenoh.NewConfigDefault(), nil)
		if err != nil {
			zenohInitError = err
			return
		}
		zenohPub = &zenohSessionPublisher{session: session}
	})
	if zenohInitError != nil {
		return nil, zenohInitError
	}
	return zenohPub, nil
}

func PublishRobotAction(payload []byte) error {
	pub, err := getZenohPublisher()
	if err != nil {
		return err
	}
	return pub.Publish(RobotActionTopic, payload)
}

// allowedActions returns the skill/action allowlist from ALLOWED_ACTIONS
// (comma-separated). An unset or empty allowlist means every action is
// rejected -- this is a fail-closed default, not fail-open.
func allowedActions() map[string]bool {
	raw := os.Getenv("ALLOWED_ACTIONS")
	set := make(map[string]bool)
	for _, a := range strings.Split(raw, ",") {
		a = strings.TrimSpace(a)
		if a != "" {
			set[a] = true
		}
	}
	return set
}

// actionRequest is the subset of the incoming payload this handler must
// understand to make an admission decision: which skill/action is being
// requested. Everything else is opaque and forwarded to the robot as-is.
type actionRequest struct {
	Action string          `json:"action"`
	Params json.RawMessage `json:"params,omitempty"`
}

type Handlers struct {
	Logger *zap.Logger
	Store  *IdempotencyStore
}

func NewHandlers(logger *zap.Logger) *Handlers {
	store, err := NewIdempotencyStore(os.Getenv("IDEMPOTENCY_STORE_PATH"))
	if err != nil {
		logger.Warn("failed to load idempotency store, starting with an empty one", zap.Error(err))
		store = &IdempotencyStore{path: "idempotency_store.json", data: make(map[string]*ActionStatus)}
	}
	return &Handlers{
		Logger: logger,
		Store:  store,
	}
}

// PostAction is the fail-closed, async entry point for a paid robot action.
// It never dispatches an action that is missing, malformed, or not on the
// configured allowlist. On success it returns 202 immediately with a fresh
// actionId; the caller polls GetActionStatus for the terminal result.
func (h *Handlers) PostAction(c *gin.Context) {
	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "failed to read request body"})
		return
	}

	if len(body) == 0 || !json.Valid(body) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "request body must be valid JSON"})
		return
	}

	var req actionRequest
	if err := json.Unmarshal(body, &req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "request body could not be parsed"})
		return
	}

	if req.Action == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "MISSING_ACTION", "message": "request is missing a required 'action' field"})
		return
	}

	allowed := allowedActions()
	if len(allowed) == 0 {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "ALLOWLIST_NOT_CONFIGURED", "message": "no actions are configured as allowed"})
		return
	}
	if !allowed[req.Action] {
		c.JSON(http.StatusForbidden, gin.H{"error": "ACTION_NOT_ALLOWED", "message": "action is not on the configured allowlist", "action": req.Action})
		return
	}

	actionID := uuid.NewString()

	_, replay, err := h.Store.Reserve(actionID)
	if replay {
		// uuid collision is astronomically unlikely, but fail closed anyway
		// rather than silently reusing another action's slot.
		c.JSON(http.StatusConflict, gin.H{"error": "ACTION_ID_COLLISION"})
		return
	}
	if err != nil {
		h.Logger.Error("failed to persist action reservation", zap.Error(err))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "RESERVATION_PERSIST_FAILED"})
		return
	}

	var paymentPayload interface{}
	if value, ok := c.Get("x402_payload"); ok {
		paymentPayload = value
	}
	var paymentRequirements interface{}
	if value, ok := c.Get("x402_requirements"); ok {
		paymentRequirements = value
	}

	// Persist the verified-but-not-yet-settled payment data against this
	// actionId. ExecutionWatcher reads it back later and is the only place
	// that ever calls ProcessSettlement -- settlement never happens here,
	// at accept time.
	if paymentPayload != nil && paymentRequirements != nil {
		payloadBytes, errP := json.Marshal(paymentPayload)
		reqBytes, errR := json.Marshal(paymentRequirements)
		if errP == nil && errR == nil {
			if err := h.Store.SetPaymentData(actionID, payloadBytes, reqBytes); err != nil {
				h.Logger.Warn("failed to persist payment data for later settlement", zap.Error(err))
			}
		} else {
			h.Logger.Warn("failed to marshal payment data for storage", zap.Error(errP), zap.Error(errR))
		}
	}

	event := gin.H{
		"actionId":  actionID,
		"action":    req.Action,
		"params":    req.Params,
		"timestamp": time.Now().UTC().Format(time.RFC3339),
	}

	eventBytes, err := json.Marshal(event)
	if err != nil {
		h.Logger.Error("failed to marshal action event", zap.Error(err))
		_ = h.Store.UpdateResult(actionID, StateFailed, "MARSHAL_ERROR", false)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to prepare action event"})
		return
	}

	if err := PublishRobotAction(eventBytes); err != nil {
		h.Logger.Error("failed to publish action event", zap.Error(err))
		_ = h.Store.UpdateResult(actionID, StateFailed, "PUBLISH_ERROR", false)
		c.JSON(http.StatusBadGateway, gin.H{"error": "failed to dispatch action"})
		return
	}

	c.JSON(http.StatusAccepted, gin.H{
		"status":     "accepted",
		"state":      StatePending,
		"actionId":   actionID,
		"status_url": "/action/" + actionID + "/status",
		"timestamp":  time.Now().UTC().Format(time.RFC3339),
	})
}

// GetActionStatus serves the durable terminal (or pending) status for a
// previously accepted action, correlated by the same actionId returned
// from PostAction.
func (h *Handlers) GetActionStatus(c *gin.Context) {
	actionID := c.Param("id")
	status, ok := h.Store.Get(actionID)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "UNKNOWN_ACTION_ID"})
		return
	}
	c.JSON(http.StatusOK, status)
}
