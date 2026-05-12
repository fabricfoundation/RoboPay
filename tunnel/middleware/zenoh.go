package middleware

import (
	"encoding/json"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"go.uber.org/zap"

	"github.com/fabricfoundation/robot-tunnel-client/tunnel"
)

// Publisher is an abstraction for publishing messages (e.g. to Zenoh).
// Implementations must be safe for concurrent use.
type Publisher interface {
	Publish(keyExpr string, payload []byte) error
}

// ZenohSessionPublisher implements Publisher using a Zenoh session.
type ZenohSessionPublisher struct {
	session zenoh.Session
}

// NewZenohSessionPublisher wraps an open Zenoh session as a Publisher.
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

// ZenohPublishMiddleware returns a middleware that publishes a ZenohEvent
// to the given key expression for every request that passes through it.
// The middleware runs after the handler so the response status is included.
func ZenohPublishMiddleware(pub Publisher, keyExpr string, logger *zap.Logger) tunnel.Middleware {
	return func(next tunnel.Handler) tunnel.Handler {
		return func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
			status, respHeaders, respBody := next(method, headers, body)

			event := ZenohEvent{
				Method:  method,
				Path:    keyExpr,
				Headers: headers,
				Body:    body,
				Status:  status,
			}
			data, err := json.Marshal(event)
			if err != nil {
				logger.Warn("zenoh middleware: failed to marshal event", zap.Error(err))
				return status, respHeaders, respBody
			}

			if err := pub.Publish(keyExpr, data); err != nil {
				logger.Warn("zenoh middleware: publish failed",
					zap.String("key", keyExpr),
					zap.Error(err),
				)
			} else {
				logger.Debug("zenoh middleware: published event",
					zap.String("key", keyExpr),
					zap.String("method", method),
					zap.Int("status", status),
				)
			}

			return status, respHeaders, respBody
		}
	}
}
