package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	"github.com/x402-foundation/x402/go/types"
	"go.uber.org/zap"
)

func TestRequestRateLimitReturns429(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv("ACTION_RATE_LIMIT_RPM", "2")

	router := gin.New()
	router.Use(requestRateLimit())
	router.GET("/action", func(c *gin.Context) {
		c.Status(http.StatusOK)
	})

	for requestNumber := 1; requestNumber <= 3; requestNumber++ {
		request := httptest.NewRequest(http.MethodGet, "/action", nil)
		request.RemoteAddr = "198.51.100.10:12345"
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		expected := http.StatusOK
		if requestNumber == 3 {
			expected = http.StatusTooManyRequests
		}
		if response.Code != expected {
			t.Fatalf("request %d: expected HTTP %d, got %d", requestNumber, expected, response.Code)
		}
	}
}

func TestParseAllowedSkills(t *testing.T) {
	known := map[string]struct{}{"navigate_obstacle_course": {}, "stop": {}}
	allowed := parseAllowedSkills(" navigate_obstacle_course, stop, INVALID-SKILL!, navigate_obstacle_course, ", known)

	if len(allowed) != 2 {
		t.Fatalf("expected two configured skills, got %d", len(allowed))
	}
	for _, skill := range []string{"navigate_obstacle_course", "stop"} {
		if _, ok := allowed[skill]; !ok {
			t.Errorf("expected %q to be allowed", skill)
		}
	}
}

func TestParseAllowedSkillsIsRobotAgnostic(t *testing.T) {
	allowed := parseAllowedSkills("look_at_apple", map[string]struct{}{"look_at_apple": {}})
	if _, ok := allowed["look_at_apple"]; !ok {
		t.Fatal("expected a robot profile skill to be registered without shared-code changes")
	}
}

func TestParseAllowedSkillsEmptyFailsClosed(t *testing.T) {
	if allowed := parseAllowedSkills(" , ", map[string]struct{}{"stop": {}}); len(allowed) != 0 {
		t.Fatalf("expected an empty allowlist, got %d skills", len(allowed))
	}
}

func TestAllowedSkillsFromUnsetEnvFailsClosed(t *testing.T) {
	if allowed := allowedSkillsFromEnv("", false, map[string]struct{}{"stop": {}}); allowed != nil {
		t.Fatalf("expected no allowlist when ALLOWED_ACTIONS is unset, got %d skills", len(allowed))
	}
}

// TestDeferredSettlementGateRejectsInvalidFacilitatorVerification proves the
// action boundary remains closed when a facilitator responds 200 but rejects
// the submitted payment (including a malformed/nil verification response).
// The x402 SDK converts both cases into ResultPaymentError before the Gin
// middleware can reach the action handler.
func TestDeferredSettlementGateRejectsInvalidFacilitatorVerification(t *testing.T) {
	gin.SetMode(gin.TestMode)

	tests := []struct {
		name         string
		verification *x402.VerifyResponse
	}{
		{
			name:         "nil verification response",
			verification: nil,
		},
		{
			name: "facilitator rejects tampered signature",
			verification: &x402.VerifyResponse{
				IsValid:        false,
				InvalidReason:  "reviewer-tampered-payment",
				InvalidMessage: "signature does not match authorization",
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			facilitator := &invalidVerificationFacilitator{verification: test.verification}
			paymentServer := x402http.Newx402HTTPResourceServer(
				x402http.RoutesConfig{
					"POST /action": {
						Accepts: x402http.PaymentOptions{{
							Scheme:  "exact",
							Price:   "$0.001",
							Network: x402.Network("eip155:84532"),
							PayTo:   "0x1111111111111111111111111111111111111111",
						}},
					},
				},
				x402.WithFacilitatorClient(facilitator),
			)
			paymentServer.Register(x402.Network("eip155:84532"), invalidVerificationTestScheme{})
			if err := paymentServer.Initialize(context.Background()); err != nil {
				t.Fatalf("initialize payment server: %v", err)
			}

			router := gin.New()
			router.Use(deferredSettlementGate(paymentServer, zap.NewNop()))
			handlerEntries := 0
			router.POST("/action", func(c *gin.Context) {
				handlerEntries++
				c.Status(http.StatusAccepted)
			})

			payload := x402.PaymentPayload{
				X402Version: 2,
				Payload:     map[string]interface{}{"authorization": "tampered-signature"},
				Accepted: x402.PaymentRequirements{
					Scheme:            "exact",
					Network:           "eip155:84532",
					Asset:             "USDC",
					Amount:            "1000",
					PayTo:             "0x1111111111111111111111111111111111111111",
					MaxTimeoutSeconds: 60,
				},
			}
			payloadJSON, err := json.Marshal(payload)
			if err != nil {
				t.Fatalf("marshal payment payload: %v", err)
			}

			request := httptest.NewRequest(http.MethodPost, "/action", nil)
			request.Header.Set("PAYMENT-SIGNATURE", base64.StdEncoding.EncodeToString(payloadJSON))
			response := httptest.NewRecorder()
			router.ServeHTTP(response, request)

			if response.Code != http.StatusPaymentRequired {
				t.Fatalf("expected HTTP %d, got %d", http.StatusPaymentRequired, response.Code)
			}
			if facilitator.verifyCalls != 1 {
				t.Fatalf("expected exactly one verification call, got %d", facilitator.verifyCalls)
			}
			if handlerEntries != 0 {
				t.Fatalf("invalid payment reached action handler %d time(s)", handlerEntries)
			}
			if facilitator.settleCalls != 0 {
				t.Fatalf("invalid payment triggered %d settlement call(s)", facilitator.settleCalls)
			}
		})
	}
}

type invalidVerificationTestScheme struct{}

func (invalidVerificationTestScheme) Scheme() string { return "exact" }

func (invalidVerificationTestScheme) ParsePrice(x402.Price, x402.Network) (x402.AssetAmount, error) {
	return x402.AssetAmount{Asset: "USDC", Amount: "1000"}, nil
}

func (invalidVerificationTestScheme) EnhancePaymentRequirements(
	_ context.Context,
	requirements types.PaymentRequirements,
	_ types.SupportedKind,
	_ []string,
) (types.PaymentRequirements, error) {
	return requirements, nil
}

type invalidVerificationFacilitator struct {
	verification *x402.VerifyResponse
	verifyCalls  int
	settleCalls  int
}

func (f *invalidVerificationFacilitator) Verify(_ context.Context, _, _ []byte) (*x402.VerifyResponse, error) {
	f.verifyCalls++
	return f.verification, nil
}

func (f *invalidVerificationFacilitator) Settle(_ context.Context, _, _ []byte) (*x402.SettleResponse, error) {
	f.settleCalls++
	return &x402.SettleResponse{Success: true}, nil
}

func (f *invalidVerificationFacilitator) GetSupported(context.Context) (x402.SupportedResponse, error) {
	return x402.SupportedResponse{
		Kinds: []x402.SupportedKind{{
			X402Version: 2,
			Scheme:      "exact",
			Network:     "eip155:84532",
		}},
		Signers: map[string][]string{},
	}, nil
}
