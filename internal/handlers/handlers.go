package handlers

import (
	"encoding/json"
	"io"
	"net/http"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

type ZenohPublisher interface {
	Publish(keyExpr string, payload []byte) error
}

type Handlers struct {
	ZenohPublisher ZenohPublisher
	Logger         *zap.Logger
}

func NewHandlers(zenohPublisher ZenohPublisher, logger *zap.Logger) *Handlers {
	return &Handlers{
		ZenohPublisher: zenohPublisher,
		Logger:         logger,
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
	} else if h.ZenohPublisher != nil {
		if err := h.ZenohPublisher.Publish("robot/tunnel/action", eventBytes); err != nil {
			h.Logger.Warn("failed to publish action event", zap.Error(err))
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"status":    "accepted",
		"timestamp": time.Now().Format(time.RFC3339),
	})
}
