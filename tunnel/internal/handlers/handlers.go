package handlers

import (
	"encoding/json"
	"errors"
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

	// maxActionBodyBytes caps paid action requests at 1 MiB so a client can
	// never force unbounded memory use on the tunnel's paid endpoint.
	maxActionBodyBytes = 1 << 20
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

type Handlers struct {
	Logger *zap.Logger
}

func NewHandlers(logger *zap.Logger) *Handlers {
	return &Handlers{
		Logger: logger,
	}
}

func (h *Handlers) PostAction(c *gin.Context) {
	// Cap the request body so a payer can never stream an unbounded payload
	// at the paid endpoint.
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxActionBodyBytes)

	body, err := io.ReadAll(c.Request.Body)
	if err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			c.JSON(http.StatusRequestEntityTooLarge, gin.H{"error": "request body exceeds the 1 MiB limit"})
			return
		}
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
	} else {
		// The event carries the payer's signed x402 authorization inside
		// transaction_details.payment_payload — never log it at Info. Log a
		// redacted summary here; the full event is available at Debug for
		// local troubleshooting only.
		h.Logger.Debug("publishing action event", zap.Any("event", event))
		h.Logger.Info("publishing action event",
			zap.String("topic", RobotActionTopic),
			zap.String("timestamp", event["timestamp"].(string)),
			zap.Bool("payment_payload_present", paymentPayload != nil),
			zap.Int("event_bytes", len(eventBytes)),
		)
		pub, err := getZenohPublisher()
		if err != nil {
			h.Logger.Warn("failed to initialize zenoh publisher", zap.Error(err))
		} else if err := pub.Publish(RobotActionTopic, eventBytes); err != nil {
			h.Logger.Warn("failed to publish action event", zap.Error(err))
		}
	}

	c.JSON(http.StatusOK, gin.H{
		"status":    "accepted",
		"timestamp": time.Now().Format(time.RFC3339),
	})
}
