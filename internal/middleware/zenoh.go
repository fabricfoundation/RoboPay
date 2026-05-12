package middleware

import (
	"bytes"
	"encoding/json"
	"io"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"github.com/gin-gonic/gin"
	"go.uber.org/zap"
)

// ZenohSessionPublisher publishes messages using a Zenoh session.
// It is safe for concurrent use as long as the underlying session is.
type ZenohSessionPublisher struct {
	session zenoh.Session
}

// NewZenohSessionPublisher wraps an open Zenoh session.
func NewZenohSessionPublisher(session zenoh.Session) *ZenohSessionPublisher {
	return &ZenohSessionPublisher{session: session}
}

// Publish puts the payload on the given key expression via the Zenoh session.
func (z *ZenohSessionPublisher) Publish(keyExpr string, payload []byte) error {
	ke, err := zenoh.NewKeyExpr(keyExpr)
	if err != nil {
		return err
	}
	return z.session.Put(ke, zenoh.NewZBytes(payload), nil)
}

// ZenohEvent is the JSON payload published for each matching request.
type ZenohEvent struct {
	Method  string            `json:"method"`
	Path    string            `json:"path"`
	Headers map[string]string `json:"headers,omitempty"`
	Body    json.RawMessage   `json:"body,omitempty"`
	Status  int               `json:"status"`
}

// ZenohPublishMiddleware returns a gin middleware that publishes a ZenohEvent
// to the given key expression for every request that passes through it.
// The middleware runs after the handler so the response status is included.
func ZenohPublishMiddleware(pub *ZenohSessionPublisher, keyExpr string, logger *zap.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		// Read body to publish
		var bodyBytes []byte
		if c.Request.Body != nil {
			bodyBytes, _ = io.ReadAll(c.Request.Body)
			// Restore the io.ReadCloser to its original state
			c.Request.Body = io.NopCloser(bytes.NewReader(bodyBytes))
		}

		// Process request
		c.Next()

		// Extract headers
		headers := make(map[string]string)
		for k, v := range c.Request.Header {
			if len(v) > 0 {
				headers[k] = v[0]
			}
		}

		event := ZenohEvent{
			Method:  c.Request.Method,
			Path:    c.Request.URL.Path,
			Headers: headers,
			Status:  c.Writer.Status(),
		}

		if len(bodyBytes) > 0 {
			event.Body = bodyBytes
		}

		data, err := json.Marshal(event)
		if err != nil {
			logger.Warn("zenoh middleware: failed to marshal event", zap.Error(err))
			return
		}

		if err := pub.Publish(keyExpr, data); err != nil {
			logger.Warn("zenoh middleware: publish failed",
				zap.String("key", keyExpr),
				zap.Error(err),
			)
		}
	}
}
