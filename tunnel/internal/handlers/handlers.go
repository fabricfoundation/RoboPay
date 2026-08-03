package handlers

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/fabricfoundation/tunnel/internal/ledger"
	"github.com/fabricfoundation/tunnel/internal/skillbook"
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

// Handlers holds the shared dependencies every HTTP handler needs:
// logger, the skill allowlist, and the action ledger.
type Handlers struct {
	Logger    *zap.Logger
	Skillbook *skillbook.Book
	Ledger    *ledger.Ledger
}

func NewHandlers(logger *zap.Logger, book *skillbook.Book, ldg *ledger.Ledger) *Handlers {
	return &Handlers{
		Logger:    logger,
		Skillbook: book,
		Ledger:    ldg,
	}
}

// postActionRequest is the client-supplied body for POST /action.
// actionId is deliberately NOT accepted from the client -- the tunnel
// generates it, so a single idempotencyKey always maps to exactly one
// server-issued actionId (see Ledger.Reserve).
type postActionRequest struct {
	RobotID        string                 `json:"robotId"`
	SkillID        string                 `json:"skillId"`
	IdempotencyKey string                 `json:"idempotencyKey"`
	Params         map[string]interface{} `json:"params"`
}

// hashParams produces a stable hex digest of the params map so the
// ledger can record what was actually requested without storing the
// raw (possibly large/sensitive) params blob itself.
func hashParams(params map[string]interface{}) (string, error) {
	if params == nil {
		params = map[string]interface{}{}
	}
	canonical, err := json.Marshal(params)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(canonical)
	return hex.EncodeToString(sum[:]), nil
}

// newActionID generates a random 16-byte hex actionId. Server-generated
// so replays are only ever recognized via idempotencyKey, never by a
// client-supplied actionId colliding by chance or by design.
func newActionID() (string, error) {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

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

	var req postActionRequest
	if err := json.Unmarshal(body, &req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid request schema"})
		return
	}

	if req.RobotID == "" || req.IdempotencyKey == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "robotId and idempotencyKey are required"})
		return
	}

	// Fail-closed: unknown skillId is rejected before anything reaches
	// the robot or the ledger.
	skill, err := h.Skillbook.Resolve(req.SkillID)
	if err != nil {
		c.JSON(http.StatusUnprocessableEntity, gin.H{"error": "unknown skillId", "skillId": req.SkillID})
		return
	}

	paramsHash, err := hashParams(req.Params)
	if err != nil {
		h.Logger.Warn("failed to hash params", zap.Error(err))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to process params"})
		return
	}

	actionID, err := newActionID()
	if err != nil {
		h.Logger.Error("failed to generate actionId", zap.Error(err))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to generate actionId"})
		return
	}

	entry, err := h.Ledger.Reserve(actionID, req.RobotID, skill.SkillID, req.IdempotencyKey, paramsHash)
	if err != nil {
		if errors.Is(err, ledger.ErrDuplicate) {
			// Replay: same idempotencyKey seen before. Do NOT re-actuate.
			// We don't have a byIdem->Entry lookup exposed yet, so for
			// now tell the client plainly this is a duplicate; wiring an
			// actual "return the original entry" path is a small ledger
			// addition (GetByIdempotencyKey) if we want full replay-safe
			// status passthrough here.
			c.JSON(http.StatusConflict, gin.H{
				"error":          "duplicate idempotencyKey",
				"idempotencyKey": req.IdempotencyKey,
			})
			return
		}
		h.Logger.Error("failed to reserve ledger entry", zap.Error(err))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to reserve action"})
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
	if err := h.Ledger.AttachPayment(actionID, paymentPayload, paymentRequirements); err != nil {
		h.Logger.Warn("failed to attach payment to ledger entry", zap.Error(err))
	}

	event := gin.H{
		"actionId":       entry.ActionID,
		"robotId":        entry.RobotID,
		"skillId":        entry.SkillID,
		"idempotencyKey": entry.IdempotencyKey,
		"paramsHash":     entry.ParamsHash,
		"params":         req.Params,
		"timestamp":      time.Now().UTC().Format(time.RFC3339),
	}

	eventBytes, err := json.Marshal(event)
	if err != nil {
		h.Logger.Warn("failed to marshal action event", zap.Error(err))
	} else {
		pub, err := getZenohPublisher()
		if err != nil {
			h.Logger.Warn("failed to initialize zenoh publisher", zap.Error(err))
		} else if err := pub.Publish(RobotActionTopic, eventBytes); err != nil {
			h.Logger.Warn("failed to publish action event", zap.Error(err))
		}
	}

	c.JSON(http.StatusAccepted, gin.H{
		"status":    "pending",
		"actionId":  entry.ActionID,
		"timestamp": entry.CreatedAt.Format(time.RFC3339),
	})
}

// GetActionStatus handles GET /action/:actionId/status. It reads only
// from the ledger -- no Zenoh/network calls -- so it stays fast under
// polling.
func (h *Handlers) GetActionStatus(c *gin.Context) {
	actionID := c.Param("actionId")
	if actionID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "actionId is required"})
		return
	}

	entry, err := h.Ledger.Get(actionID)
	if err != nil {
		if errors.Is(err, ledger.ErrNotFound) {
			c.JSON(http.StatusNotFound, gin.H{"error": "action not found", "actionId": actionID})
			return
		}
		h.Logger.Error("failed to read ledger entry", zap.Error(err))
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to read action status"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"actionId":      entry.ActionID,
		"robotId":       entry.RobotID,
		"skillId":       entry.SkillID,
		"status":        entry.State,
		"resultMessage": entry.ResultMessage,
		"errorCode":     entry.ErrorCode,
		"errorMessage":  entry.ErrorMessage,
		"settled":       entry.Settled,
		"settleTx":      entry.SettleTx,
		"settleNetwork": entry.SettleNetwork,
		"createdAt":     entry.CreatedAt.Format(time.RFC3339),
		"updatedAt":     entry.UpdatedAt.Format(time.RFC3339),
	})
}
