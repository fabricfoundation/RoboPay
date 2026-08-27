package internal

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"runtime/debug"
	"strings"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/gorilla/websocket"
	"go.uber.org/zap"
)

const tunnelAuthPrefix = "RoboPay-Tunnel-Auth-v1"

var terminalRejections = map[string]string{
	"not_staked":             "the staking address does not hold the tier required for tunnel access",
	"invalid_signature":      "the proxy could not verify the handshake signature",
	"address_mismatch":       "the handshake signature does not match staking_address",
	"invalid_address":        "staking_address is not a valid EVM address",
	"missing_authentication": "the proxy requires a signed handshake this build did not send",
	"missing_robot_id":       "no robot id was sent",
	"robot_id_in_use":        "another robot is already connected with this id",
}

// handshakeRejection reads the proxy's explanation off a failed handshake and reports
// whether retrying could ever succeed.
func handshakeRejection(resp *http.Response) (reason, message string, terminal bool) {
	var body struct {
		Error  string `json:"error"`
		Reason string `json:"reason"`
	}

	if resp.Body != nil {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		_ = resp.Body.Close()
		if err := json.Unmarshal(raw, &body); err != nil {
			body.Error = strings.TrimSpace(string(raw))
		}
	}

	message = body.Error
	if message == "" {
		message = resp.Status
	}

	reason = body.Reason
	if reason == "" {
		switch resp.StatusCode {
		case http.StatusBadRequest, http.StatusUnauthorized, http.StatusForbidden, http.StatusConflict:
			return reason, message, true
		}
		return reason, message, false
	}

	_, terminal = terminalRejections[reason]
	return reason, message, terminal
}

// tunnelAuthMessage is what the robot signs to prove it holds the staking key.
func tunnelAuthMessage(robotID, nonce string) string {
	return tunnelAuthPrefix + "\nrobot_id:" + robotID + "\nnonce:" + nonce
}

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
	wsBaseURL      string
	robotID        string
	stakingAddress string
	stakingKey     *ecdsa.PrivateKey
	handler        http.Handler
	dialer         *websocket.Dialer

	writeMu sync.Mutex
	logger  *zap.Logger
}

func NewClient(wsBaseURL string, robotID string, stakingAddress string, stakingKey *ecdsa.PrivateKey,
	handler http.Handler, logger *zap.Logger) *Client {
	return &Client{
		wsBaseURL:      wsBaseURL,
		robotID:        robotID,
		stakingAddress: stakingAddress,
		stakingKey:     stakingKey,
		handler:        handler,
		logger:         logger,
		dialer:         websocket.DefaultDialer,
	}
}

// nonceURL derives the proxy's nonce endpoint from the WebSocket base URL, so the
// two cannot drift apart in configuration.
func (c *Client) nonceURL() (string, error) {
	u, err := url.Parse(c.wsBaseURL)
	if err != nil {
		return "", fmt.Errorf("invalid ws base url %q: %w", c.wsBaseURL, err)
	}
	switch u.Scheme {
	case "wss":
		u.Scheme = "https"
	default:
		u.Scheme = "http"
	}
	u.Path = strings.TrimRight(u.Path, "/") + "/nonce"
	u.RawQuery = ""
	return u.String(), nil
}

// fetchNonce asks the proxy for a single-use handshake nonce.
func (c *Client) fetchNonce(ctx context.Context) (string, error) {
	endpoint, err := c.nonceURL()
	if err != nil {
		return "", err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return "", err
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("failed to fetch handshake nonce: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("nonce endpoint returned %d", resp.StatusCode)
	}

	var body struct {
		Nonce string `json:"nonce"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return "", fmt.Errorf("failed to decode nonce response: %w", err)
	}
	if body.Nonce == "" {
		return "", fmt.Errorf("nonce endpoint returned an empty nonce")
	}
	return body.Nonce, nil
}

// signNonce produces the personal_sign signature the proxy recovers the staking
// address from.
func (c *Client) signNonce(nonce string) (string, error) {
	if c.stakingKey == nil {
		return "", fmt.Errorf("no staking key configured")
	}

	hash := accounts.TextHash([]byte(tunnelAuthMessage(c.robotID, nonce)))
	sig, err := crypto.Sign(hash, c.stakingKey)
	if err != nil {
		return "", fmt.Errorf("failed to sign handshake nonce: %w", err)
	}

	sig[64] += 27
	return "0x" + hex.EncodeToString(sig), nil
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
			if resp != nil {
				reason, message, terminal := handshakeRejection(resp)
				if terminal {
					c.logger.Fatal("tunnel handshake refused; not retrying",
						zap.Int("status", resp.StatusCode),
						zap.String("reason", reason),
						zap.String("proxy_says", message),
						zap.String("staking_address", c.stakingAddress),
						zap.String("robot_id", c.robotID),
						zap.String("explanation", terminalRejections[reason]),
					)
				}
				c.logger.Warn("ws dial failed; will retry",
					zap.Int("status", resp.StatusCode),
					zap.String("reason", reason),
					zap.String("proxy_says", message),
				)
			} else {
				c.logger.Warn("ws dial failed; will retry", zap.Error(err))
			}
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

	nonce, err := c.fetchNonce(ctx)
	if err != nil {
		return nil, nil, err
	}
	signature, err := c.signNonce(nonce)
	if err != nil {
		return nil, nil, err
	}

	query := proxyURL.Query()
	query.Set("id", c.robotID)
	query.Set("address", c.stakingAddress)
	query.Set("nonce", nonce)
	query.Set("signature", signature)
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

	c.logger.Info("sending response envelope", zap.String("id", request.ID), zap.String("method", request.Method), zap.String("path", request.Path), zap.Any("headers", response.Headers), zap.Int("status", response.Status))

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
