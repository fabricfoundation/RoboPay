package internal

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"runtime/debug"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"go.uber.org/zap"
)

type Envelope struct {
	Type    string            `json:"type"`
	ID      string            `json:"id"`
	Method  string            `json:"method,omitempty"`
	Path    string            `json:"path,omitempty"`
	Headers map[string]string `json:"headers,omitempty"`
	Status  int               `json:"status,omitempty"`
	Body    []byte            `json:"body,omitempty"`
	Error   string            `json:"error,omitempty"`
}

type Client struct {
	wsBaseURL string
	robotID   string
	handler   http.Handler
	dialer    *websocket.Dialer

	writeMu sync.Mutex
	logger  *zap.Logger
}

func NewClient(wsBaseURL string, robotID string, handler http.Handler, logger *zap.Logger) *Client {
	return &Client{
		wsBaseURL: wsBaseURL,
		robotID:   robotID,
		handler:   handler,
		logger:    logger,
		dialer:    websocket.DefaultDialer,
	}
}

func (c *Client) Run(ctx context.Context) {
	backoff := time.Second

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		conn, resp, err := c.dial(ctx)
		if err != nil {
			if resp != nil && resp.StatusCode == http.StatusConflict {
				c.logger.Fatal("robot ID already connected to proxy (409 Conflict)", zap.Error(err))
			}
			c.logger.Warn("ws dial failed", zap.Error(err))
			if !sleepWithContext(ctx, backoff) {
				return
			}
			backoff = nextBackoff(backoff)
			continue
		}

		c.logger.Info("ws connected to proxy", zap.String("robot_id", c.robotID))
		backoff = time.Second

		go func() {
			<-ctx.Done()
			_ = conn.Close()
		}()

		err = c.readLoop(ctx, conn)
		if err != nil && ctx.Err() == nil {
			c.logger.Warn("ws disconnected", zap.Error(err))
		}
		_ = conn.Close()
	}
}

func (c *Client) dial(ctx context.Context) (*websocket.Conn, *http.Response, error) {
	proxyURL, err := url.Parse(c.wsBaseURL)
	if err != nil {
		return nil, nil, fmt.Errorf("invalid ws base url %q: %w", c.wsBaseURL, err)
	}

	query := proxyURL.Query()
	query.Set("id", c.robotID)
	proxyURL.RawQuery = query.Encode()

	headers := make(http.Header)
	conn, resp, err := c.dialer.DialContext(ctx, proxyURL.String(), headers)
	if err != nil {
		if resp != nil {
			return nil, resp, err
		}
		return nil, nil, err
	}

	return conn, resp, nil
}

func (c *Client) readLoop(ctx context.Context, conn *websocket.Conn) error {
	for {
		_, message, err := conn.ReadMessage()
		if err != nil {
			return err
		}

		var envelope Envelope
		if err := json.Unmarshal(message, &envelope); err != nil {
			c.logger.Warn("invalid envelope json", zap.Error(err))
			continue
		}

		if envelope.Type != "request" {
			c.logger.Warn("ignoring non-request envelope", zap.String("type", envelope.Type), zap.String("id", envelope.ID))
			continue
		}

		request := envelope
		go c.dispatchRequest(ctx, conn, request)
	}
}

func (c *Client) dispatchRequest(ctx context.Context, conn *websocket.Conn, request Envelope) {
	response := Envelope{
		Type: "response",
		ID:   request.ID,
	}

	defer func() {
		if recovered := recover(); recovered != nil {
			response.Status = 500
			response.Error = fmt.Sprintf("handler panic: %v", recovered)
			c.logger.Error("handler panic", zap.String("path", request.Path), zap.String("id", request.ID), zap.Any("panic", recovered))
			c.logger.Error("stack trace", zap.String("stack", string(debug.Stack())))
		}

		if err := c.writeEnvelope(conn, response); err != nil {
			if ctx.Err() != nil {
				return
			}
			c.logger.Error("response send failed", zap.String("id", request.ID), zap.String("path", request.Path), zap.Error(err))
			_ = conn.Close()
		}
	}()

	reqURL, err := url.Parse(request.Path)
	if err != nil {
		response.Status = http.StatusBadRequest
		response.Error = fmt.Sprintf("invalid path: %v", err)
		return
	}

	req := &http.Request{
		Method: request.Method,
		URL:    reqURL,
		Header: make(http.Header),
	}
	req.Body = io.NopCloser(bytes.NewReader(request.Body))

	for k, v := range request.Headers {
		req.Header.Set(k, v)
	}

	recorder := httptest.NewRecorder()
	c.handler.ServeHTTP(recorder, req)

	res := recorder.Result()
	response.Status = res.StatusCode
	response.Headers = make(map[string]string)
	for k, v := range res.Header {
		if len(v) > 0 {
			response.Headers[k] = v[0]
		}
	}

	c.logger.Info("sending response envelope", zap.String("id", request.ID), zap.Any("headers", response.Headers), zap.Int("status", response.Status))

	bodyBytes, _ := io.ReadAll(res.Body)
	response.Body = bodyBytes
}

func (c *Client) writeEnvelope(conn *websocket.Conn, envelope Envelope) error {
	c.writeMu.Lock()
	defer c.writeMu.Unlock()

	return conn.WriteJSON(envelope)
}

func sleepWithContext(ctx context.Context, duration time.Duration) bool {
	timer := time.NewTimer(duration)
	defer timer.Stop()

	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func nextBackoff(current time.Duration) time.Duration {
	if current >= 30*time.Second {
		return 30 * time.Second
	}

	next := current * 2
	if next > 30*time.Second {
		return 30 * time.Second
	}

	return next
}
