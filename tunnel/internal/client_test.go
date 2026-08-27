package internal

import (
	"context"
	"encoding/hex"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/accounts"
	"github.com/ethereum/go-ethereum/crypto"
	"go.uber.org/zap"
)

func TestDialInvalidBaseURL(t *testing.T) {
	client := NewClient("://bad-url", "robot-1", "0x4323c31635704bd076736d5205b747946D1BbAB7", nil, nil, zap.NewNop())

	_, _, err := client.dial(context.Background())
	if err == nil {
		t.Fatal("expected error for invalid ws base url")
	}
}

func TestSleepWithContextCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if ok := sleepWithContext(ctx, 500*time.Millisecond); ok {
		t.Fatal("expected sleepWithContext to return false when context is cancelled")
	}
}

func TestSleepWithContextCompletes(t *testing.T) {
	ctx := context.Background()

	if ok := sleepWithContext(ctx, 5*time.Millisecond); !ok {
		t.Fatal("expected sleepWithContext to return true when timer completes")
	}
}

func TestNextBackoff(t *testing.T) {
	if got := nextBackoff(1 * time.Second); got != 2*time.Second {
		t.Fatalf("expected 2s, got %v", got)
	}
	if got := nextBackoff(16 * time.Second); got != 30*time.Second {
		t.Fatalf("expected cap at 30s, got %v", got)
	}
	if got := nextBackoff(30 * time.Second); got != 30*time.Second {
		t.Fatalf("expected cap to remain at 30s, got %v", got)
	}
}

// The signed message must match the proxy's constant byte for byte — it is the
// message the proxy hashes to recover the address it gates on.
func TestTunnelAuthMessageFormat(t *testing.T) {
	got := tunnelAuthMessage("robot-1", "deadbeef")
	want := "RoboPay-Tunnel-Auth-v1\nrobot_id:robot-1\nnonce:deadbeef"
	if got != want {
		t.Fatalf("message = %q, want %q", got, want)
	}
}

// Signing is what proves control of the staking address: the proxy derives the address
// from this signature rather than trusting anything we send. Recovering it here is the
// same operation the proxy performs.
func TestSignNonceRecoversStakingAddress(t *testing.T) {
	key, err := crypto.HexToECDSA("59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
	if err != nil {
		t.Fatal(err)
	}
	want := crypto.PubkeyToAddress(key.PublicKey)

	c := NewClient("ws://localhost:8080/api/core/ws/robot", "robot-1", want.Hex(), key, nil, zap.NewNop())

	signature, err := c.signNonce("deadbeef")
	if err != nil {
		t.Fatalf("signNonce: %v", err)
	}

	raw, err := hex.DecodeString(strings.TrimPrefix(signature, "0x"))
	if err != nil {
		t.Fatalf("signature is not hex: %v", err)
	}
	if len(raw) != 65 {
		t.Fatalf("signature is %d bytes, want 65", len(raw))
	}
	if raw[64] < 27 {
		t.Fatalf("V = %d, want 27/28 so personal_sign consumers accept it", raw[64])
	}
	raw[64] -= 27

	pub, err := crypto.SigToPub(accounts.TextHash([]byte(tunnelAuthMessage("robot-1", "deadbeef"))), raw)
	if err != nil {
		t.Fatalf("recover: %v", err)
	}
	if got := crypto.PubkeyToAddress(*pub); got != want {
		t.Fatalf("recovered %s, want %s", got.Hex(), want.Hex())
	}
}

// A signature is bound to one robot id and one nonce, so neither can be swapped.
func TestSignNonceIsBoundToRobotAndNonce(t *testing.T) {
	key, _ := crypto.HexToECDSA("59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
	addr := crypto.PubkeyToAddress(key.PublicKey)

	base, _ := NewClient("ws://x", "robot-1", addr.Hex(), key, nil, zap.NewNop()).signNonce("nonce-a")
	otherRobot, _ := NewClient("ws://x", "robot-2", addr.Hex(), key, nil, zap.NewNop()).signNonce("nonce-a")
	otherNonce, _ := NewClient("ws://x", "robot-1", addr.Hex(), key, nil, zap.NewNop()).signNonce("nonce-b")

	if base == otherRobot {
		t.Fatal("signature is identical across robot ids; it is not bound to the robot")
	}
	if base == otherNonce {
		t.Fatal("signature is identical across nonces; it is replayable")
	}
}

// The nonce endpoint is derived from the ws base URL so the two cannot drift.
func TestNonceURLDerivation(t *testing.T) {
	for base, want := range map[string]string{
		"ws://localhost:8080/api/core/ws/robot":   "http://localhost:8080/api/core/ws/robot/nonce",
		"wss://api.example.com/api/core/ws/robot": "https://api.example.com/api/core/ws/robot/nonce",
		"ws://localhost:8080/api/core/ws/robot/":  "http://localhost:8080/api/core/ws/robot/nonce",
	} {
		got, err := NewClient(base, "robot-1", "", nil, nil, zap.NewNop()).nonceURL()
		if err != nil {
			t.Fatalf("nonceURL(%q): %v", base, err)
		}
		if got != want {
			t.Fatalf("nonceURL(%q) = %q, want %q", base, got, want)
		}
	}
}

// A refused handshake must be classified correctly: a wallet that is not staked can
// never be fixed by reconnecting, while a staking RPC outage can.
func TestHandshakeRejectionClassification(t *testing.T) {
	cases := []struct {
		name         string
		status       int
		body         string
		wantTerminal bool
		wantMessage  string
	}{
		{
			name:         "not staked is terminal",
			status:       http.StatusForbidden,
			body:         `{"error":"wallet 0xAAA does not hold the staking tier required for tunnel access","reason":"not_staked"}`,
			wantTerminal: true,
			wantMessage:  "wallet 0xAAA does not hold the staking tier required for tunnel access",
		},
		{
			name:         "invalid signature is terminal",
			status:       http.StatusUnauthorized,
			body:         `{"error":"invalid tunnel signature","reason":"invalid_signature"}`,
			wantTerminal: true,
			wantMessage:  "invalid tunnel signature",
		},
		{
			name:         "robot id in use is terminal",
			status:       http.StatusConflict,
			body:         `{"error":"robot id already connected","reason":"robot_id_in_use"}`,
			wantTerminal: true,
			wantMessage:  "robot id already connected",
		},
		{
			// A raced or expired nonce resolves itself on the next attempt.
			name:         "nonce rejection is retryable",
			status:       http.StatusUnauthorized,
			body:         `{"error":"nonce is unknown, expired, or already used","reason":"nonce_rejected"}`,
			wantTerminal: false,
			wantMessage:  "nonce is unknown, expired, or already used",
		},
		{
			name:         "staking rpc outage is retryable",
			status:       http.StatusBadGateway,
			body:         `{"error":"failed to verify staking eligibility","reason":"staking_unavailable"}`,
			wantTerminal: false,
			wantMessage:  "failed to verify staking eligibility",
		},
		{
			// A proxy that predates reason codes still must not loop on a 403.
			name:         "403 without a reason code is terminal",
			status:       http.StatusForbidden,
			body:         `{"error":"wallet address is not eligible for tunnel access"}`,
			wantTerminal: true,
			wantMessage:  "wallet address is not eligible for tunnel access",
		},
		{
			name:         "503 without a reason code is retryable",
			status:       http.StatusServiceUnavailable,
			body:         `service unavailable`,
			wantTerminal: false,
			wantMessage:  "service unavailable",
		},
		{
			name:         "empty body falls back to the status line",
			status:       http.StatusForbidden,
			body:         ``,
			wantTerminal: true,
			wantMessage:  "403 Forbidden",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := &http.Response{
				StatusCode: tc.status,
				Status:     http.StatusText(tc.status),
				Body:       io.NopCloser(strings.NewReader(tc.body)),
			}
			if tc.status == http.StatusForbidden && tc.body == "" {
				resp.Status = "403 Forbidden"
			}

			_, message, terminal := handshakeRejection(resp)
			if terminal != tc.wantTerminal {
				t.Fatalf("terminal = %v, want %v", terminal, tc.wantTerminal)
			}
			if message != tc.wantMessage {
				t.Fatalf("message = %q, want %q", message, tc.wantMessage)
			}
		})
	}
}
