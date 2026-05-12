package middleware

import (
	"encoding/json"

	"github.com/eclipse-zenoh/zenoh-go/zenoh"
	"go.uber.org/zap"

	"github.com/fabricfoundation/robot-tunnel-client/internal"
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

// ZenohPublishMiddleware returns a middleware that publishes a ZenohEvent
// to the given key expression for every request that passes through it.
// The middleware runs after the handler so the response status is included.
func ZenohPublishMiddleware(pub *ZenohSessionPublisher, keyExpr string, logger *zap.Logger) internal.Middleware {
	return func(next internal.Handler) internal.Handler {
		return func(method string, path string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
			status, respHeaders, respBody := next(method, path, headers, body)

			event := ZenohEvent{
				Method:  method,
				Path:    path,
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
			}

			return status, respHeaders, respBody
		}
	}
}
