package handlers

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

const (
	RobotActionTopic = "robot/tunnel/action"
	RobotResultTopic = "robot/tunnel/result"
	replayTTL        = 10 * time.Minute
	executionTimeout = 90 * time.Second
)

type validationError struct {
	status  int
	code    string
	message string
}

func (e validationError) Error() string {
	return fmt.Sprintf("%s: %s", e.code, e.message)
}

type actionMetadata struct {
	ActionID       string
	RobotID        string
	SkillID        string
	ParamsHash     string
	IdempotencyKey string
}

// OpenZenohSession opens the session used by both the action publisher and
// the tunnel's configuration subscriber. Tests and local deployments can
// provide a complete Zenoh JSON5 config through ZENOH_CONFIG; production
// deployments continue to use Zenoh's default configuration.
func OpenZenohSession() (zenoh.Session, error) {
	if path := os.Getenv("ZENOH_CONFIG"); path != "" {
		config, err := zenoh.NewConfigFromFile(path)
		if err != nil {
			return zenoh.Session{}, err
		}
		return zenoh.Open(config, nil)
	}
	return zenoh.Open(zenoh.NewConfigDefault(), nil)
}

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
		session, err := OpenZenohSession()
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

type Handlers struct {
	Logger             *zap.Logger
	RobotID            string
	Publisher          zenohPublisher
	AllowedSkills      map[string]struct{}
	MaxDurationSeconds float64
	// WaitForResult is injectable for contract tests. Production uses the
	// Zenoh result subscriber created below.
	WaitForResult func(actionID string) (chan bool, func(), error)

	replayMu sync.Mutex
	seenKeys map[string]time.Time
}

func NewHandlers(logger *zap.Logger) *Handlers {
	return NewHandlersForRobot(logger, "")
}

func NewHandlersForRobot(logger *zap.Logger, robotID string) *Handlers {
	return &Handlers{
		Logger:             logger,
		RobotID:            robotID,
		seenKeys:           make(map[string]time.Time),
		MaxDurationSeconds: 30,
	}
}

func (h *Handlers) publish(payload []byte) error {
	if h.Publisher != nil {
		return h.Publisher.Publish(RobotActionTopic, payload)
	}
	return PublishRobotAction(payload)
}

// prepareExecutionWait subscribes before the ActionEvent is published so a
// fast simulator cannot race past the result observer. The real x402 path
// uses this waiter; injected test publishers intentionally bypass it.
func (h *Handlers) prepareExecutionWait(actionID string) (chan bool, func(), error) {
	if h.WaitForResult != nil {
		return h.WaitForResult(actionID)
	}
	if h.Publisher != nil || actionID == "" {
		return nil, func() {}, nil
	}

	pub, err := getZenohPublisher()
	if err != nil {
		return nil, nil, err
	}
	zenohPub, ok := pub.(*zenohSessionPublisher)
	if !ok {
		return nil, nil, fmt.Errorf("zenoh publisher does not expose a session")
	}
	keyExpr, err := zenoh.NewKeyExpr(RobotResultTopic)
	if err != nil {
		return nil, nil, err
	}
	result := make(chan bool, 1)
	sub, err := zenohPub.session.DeclareSubscriber(keyExpr, zenoh.Closure[zenoh.Sample]{
		Call: func(sample zenoh.Sample) {
			var envelope struct {
				ActionID string `json:"action_id"`
				Status   string `json:"status"`
			}
			if err := json.Unmarshal(sample.Payload().Bytes(), &envelope); err != nil || envelope.ActionID != actionID {
				return
			}
			select {
			case result <- envelope.Status == "success":
			default:
			}
		},
	}, nil)
	if err != nil {
		return nil, nil, err
	}
	return result, func() { _ = sub.Undeclare() }, nil
}

func (h *Handlers) reserveReplayKey(key string) bool {
	if key == "" {
		return true
	}

	now := time.Now()
	h.replayMu.Lock()
	defer h.replayMu.Unlock()
	for existing, timestamp := range h.seenKeys {
		if now.Sub(timestamp) > replayTTL {
			delete(h.seenKeys, existing)
		}
	}
	if _, exists := h.seenKeys[key]; exists {
		return false
	}
	h.seenKeys[key] = now
	return true
}

func (h *Handlers) releaseReplayKey(key string) {
	if key == "" {
		return
	}
	h.replayMu.Lock()
	delete(h.seenKeys, key)
	h.replayMu.Unlock()
}

func stringField(object map[string]interface{}, names ...string) string {
	for _, name := range names {
		if value, ok := object[name].(string); ok {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func validatePayload(payload interface{}, expectedRobotID string) (actionMetadata, error) {
	metadata := actionMetadata{}
	object, ok := payload.(map[string]interface{})
	if !ok {
		return metadata, nil
	}

	if rawAction, present := object["action"]; present {
		action, valid := rawAction.(string)
		if !valid || strings.TrimSpace(action) == "" {
			return metadata, validationError{http.StatusBadRequest, "INVALID_ACTION", "action must be a non-empty string"}
		}
		metadata.SkillID = strings.TrimSpace(action)
		if expectedSkills := object["_allowed_skills"]; expectedSkills != nil {
			// Internal policy injection is handled by PostAction; this branch is
			// intentionally unused for user payloads.
			_ = expectedSkills
		}
	}

	if rawParams, present := object["params"]; present && rawParams != nil {
		if _, valid := rawParams.(map[string]interface{}); !valid {
			return metadata, validationError{http.StatusBadRequest, "INVALID_PARAMS", "params must be a JSON object"}
		}
	}

	if suppliedRobotID := stringField(object, "robot_id", "robotId"); suppliedRobotID != "" {
		if expectedRobotID != "" && suppliedRobotID != expectedRobotID {
			return metadata, validationError{http.StatusForbidden, "WRONG_ROBOT", "action targets a different robot"}
		}
		metadata.RobotID = suppliedRobotID
	}
	if metadata.RobotID == "" {
		metadata.RobotID = expectedRobotID
	}

	params, _ := object["params"].(map[string]interface{})
	metadata.ActionID = stringField(object, "action_id", "actionId", "id", "request_id", "requestId")
	if metadata.ActionID == "" && params != nil {
		metadata.ActionID = stringField(params, "request_id", "requestId", "correlation_id", "correlationId")
	}
	metadata.IdempotencyKey = stringField(object, "idempotency_key", "idempotencyKey")
	if metadata.IdempotencyKey == "" {
		metadata.IdempotencyKey = metadata.ActionID
	}
	if metadata.ActionID == "" {
		metadata.ActionID = fmt.Sprintf("action-%d", time.Now().UnixNano())
	}
	if metadata.IdempotencyKey == "" {
		metadata.IdempotencyKey = metadata.ActionID
	}
	if metadata.SkillID == "" {
		metadata.SkillID = stringField(object, "skill_id", "skillId")
	}

	canonicalParams, err := json.Marshal(params)
	if err != nil {
		return metadata, validationError{http.StatusBadRequest, "INVALID_PARAMS", "params could not be canonicalized"}
	}
	hash := sha256.Sum256(canonicalParams)
	metadata.ParamsHash = fmt.Sprintf("sha256:%x", hash[:])
	return metadata, nil
}

func (h *Handlers) validateExecutionPolicy(metadata actionMetadata, payload interface{}) error {
	if len(h.AllowedSkills) > 0 {
		if _, ok := h.AllowedSkills[metadata.SkillID]; !ok {
			return validationError{http.StatusForbidden, "SKILL_NOT_ALLOWED", "action is not enabled for this robot"}
		}
	}
	if h.MaxDurationSeconds <= 0 {
		return nil
	}
	object, _ := payload.(map[string]interface{})
	params, _ := object["params"].(map[string]interface{})
	if raw, ok := params["duration"]; ok {
		duration, ok := raw.(float64)
		if !ok || duration <= 0 || duration > h.MaxDurationSeconds {
			return validationError{http.StatusBadRequest, "DURATION_LIMIT", fmt.Sprintf("duration must be between 0 and %.0f seconds", h.MaxDurationSeconds)}
		}
	}
	return nil
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

	metadata, err := validatePayload(payload, h.RobotID)
	if err != nil {
		if contractErr, ok := err.(validationError); ok {
			h.Logger.Warn("invalid action contract", zap.Error(contractErr))
			c.JSON(contractErr.status, gin.H{
				"error":      contractErr.message,
				"error_code": contractErr.code,
			})
			return
		}
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid action contract", "error_code": "INVALID_CONTRACT"})
		return
	}
	if err := h.validateExecutionPolicy(metadata, payload); err != nil {
		contractErr := err.(validationError)
		h.Logger.Warn("action rejected by execution policy", zap.Error(contractErr))
		c.JSON(contractErr.status, gin.H{"error": contractErr.message, "error_code": contractErr.code})
		return
	}
	if !h.reserveReplayKey(metadata.IdempotencyKey) {
		c.JSON(http.StatusConflict, gin.H{
			"error":      "duplicate action",
			"error_code": "REPLAY_DETECTED",
			"action_id":  metadata.ActionID,
		})
		return
	}
	waitResult, cleanupWait, err := h.prepareExecutionWait(metadata.ActionID)
	if err != nil {
		h.releaseReplayKey(metadata.IdempotencyKey)
		h.Logger.Warn("failed to subscribe for simulator result", zap.Error(err))
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "result channel unavailable", "error_code": "RESULT_CHANNEL_UNAVAILABLE"})
		return
	}
	defer cleanupWait()

	var paymentPayload interface{}
	if value, ok := c.Get("x402_payload"); ok {
		paymentPayload = value
	}

	var paymentRequirements interface{}
	if value, ok := c.Get("x402_requirements"); ok {
		paymentRequirements = value
	}

	event := gin.H{
		"payload":         payload,
		"action_id":       metadata.ActionID,
		"robot_id":        metadata.RobotID,
		"skill_id":        metadata.SkillID,
		"params_hash":     metadata.ParamsHash,
		"idempotency_key": metadata.IdempotencyKey,
		"transaction_details": gin.H{
			"payment_payload":      paymentPayload,
			"payment_requirements": paymentRequirements,
		},
		"timestamp": time.Now().Format(time.RFC3339),
	}

	eventBytes, err := json.Marshal(event)
	if err != nil {
		h.Logger.Warn("failed to marshal action event", zap.Error(err))
		h.releaseReplayKey(metadata.IdempotencyKey)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to marshal action event"})
		return
	}
	if err := h.publish(eventBytes); err != nil {
		h.Logger.Warn("failed to publish action event", zap.Error(err))
		h.releaseReplayKey(metadata.IdempotencyKey)
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "failed to publish action event"})
		return
	}

	if waitResult != nil {
		select {
		case success := <-waitResult:
			if !success {
				h.releaseReplayKey(metadata.IdempotencyKey)
				c.JSON(http.StatusBadGateway, gin.H{"error": "simulator execution failed", "error_code": "SIMULATOR_EXECUTION_FAILED", "action_id": metadata.ActionID})
				return
			}
		case <-time.After(executionTimeout):
			h.releaseReplayKey(metadata.IdempotencyKey)
			c.JSON(http.StatusGatewayTimeout, gin.H{"error": "simulator result timeout", "error_code": "SIMULATOR_RESULT_TIMEOUT", "action_id": metadata.ActionID})
			return
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"status":    "accepted",
		"timestamp": time.Now().Format(time.RFC3339),
	})
}
