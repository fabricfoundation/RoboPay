package handlers

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/internal/zenohconfig"
)

const (
	RobotActionTopic = "robot/tunnel/action"
	RobotResultTopic = "robot/tunnel/result"
	ExecutionTimeout = 30 * time.Second
)

type zenohPublisher interface {
	Publish(keyExpr string, payload []byte) error
}

type zenohSessionPublisher struct{ session zenoh.Session }

func (z *zenohSessionPublisher) Publish(keyExpr string, payload []byte) error {
	ke, err := zenoh.NewKeyExpr(keyExpr)
	if err != nil {
		return err
	}
	return z.session.Put(ke, zenoh.NewZBytes(payload), nil)
}

type ExecutionResult struct {
	ActionID       string                 `json:"actionId"`
	RobotID        string                 `json:"robotId"`
	IdempotencyKey string                 `json:"idempotencyKey"`
	Status         string                 `json:"status"`
	Metrics        map[string]interface{} `json:"metrics"`
	Error          *string                `json:"error"`
	Timestamp      float64                `json:"timestamp"`
}

var (
	zenohOnce           sync.Once
	zenohPub            zenohPublisher
	zenohInitError      error
	ErrExecutionTimeout = errors.New("timed out waiting for correlated robot result")
)

func getZenohPublisher() (zenohPublisher, error) {
	zenohOnce.Do(func() {
		cfg, err := zenohconfig.FromEnvironment()
		if err != nil {
			zenohInitError = err
			return
		}
		session, err := zenoh.Open(cfg, nil)
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

// ExecuteRobotAction subscribes before publication, then waits for the exact
// actionId-correlated terminal result. Returning an error or non-SUCCESS result
// causes the protected HTTP handler to return non-2xx, so x402 cancels instead
// of settling the verified authorization.
func ExecuteRobotAction(ctx context.Context, payload []byte, actionID string) (ExecutionResult, error) {
	pub, err := getZenohPublisher()
	if err != nil {
		return ExecutionResult{}, err
	}
	transport, ok := pub.(*zenohSessionPublisher)
	if !ok {
		return ExecutionResult{}, errors.New("zenoh transport does not support correlated results")
	}
	resultExpr, err := zenoh.NewKeyExpr(RobotResultTopic)
	if err != nil {
		return ExecutionResult{}, err
	}
	resultCh := make(chan ExecutionResult, 1)
	sub, err := transport.session.DeclareSubscriber(resultExpr, zenoh.Closure[zenoh.Sample]{
		Call: func(sample zenoh.Sample) {
			var result ExecutionResult
			if json.Unmarshal(sample.Payload().Bytes(), &result) == nil && result.ActionID == actionID {
				select {
				case resultCh <- result:
				default:
				}
			}
		},
	}, nil)
	if err != nil {
		return ExecutionResult{}, err
	}
	defer func() { _ = sub.Undeclare() }()
	if err := pub.Publish(RobotActionTopic, payload); err != nil {
		return ExecutionResult{}, err
	}
	select {
	case result := <-resultCh:
		return result, nil
	case <-ctx.Done():
		return ExecutionResult{}, ErrExecutionTimeout
	}
}

type Handlers struct {
	Logger  *zap.Logger
	Execute func(context.Context, []byte, string) (ExecutionResult, error)
}

func NewHandlers(logger *zap.Logger) *Handlers {
	return &Handlers{Logger: logger, Execute: ExecuteRobotAction}
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
	var payload map[string]interface{}
	if err := json.Unmarshal(body, &payload); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "request body must be a JSON object"})
		return
	}
	actionID, ok := payload["actionId"].(string)
	if !ok || actionID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "actionId is required"})
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
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to encode action event"})
		return
	}

	ctx, cancel := context.WithTimeout(c.Request.Context(), ExecutionTimeout)
	defer cancel()
	result, err := h.Execute(ctx, eventBytes, actionID)
	if err != nil {
		status := http.StatusBadGateway
		if errors.Is(err, ErrExecutionTimeout) {
			status = http.StatusGatewayTimeout
		}
		h.Logger.Warn("robot execution did not complete", zap.String("action_id", actionID), zap.Error(err))
		c.JSON(status, gin.H{"error": err.Error(), "actionId": actionID})
		return
	}
	if result.Status != "SUCCESS" {
		status := http.StatusUnprocessableEntity
		if result.Status == "REPLAY_REJECTED" {
			status = http.StatusConflict
		}
		c.JSON(status, result)
		return
	}
	c.JSON(http.StatusOK, gin.H{"status": "executed", "result": result, "timestamp": time.Now().Format(time.RFC3339)})
}
