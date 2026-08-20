package mppay

import (
	"bytes"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/tempoxyz/mpp-go/pkg/mpp"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/config"
)

const testSecret = "test-secret-key-that-is-long-enough-for-hmac"

func testConfig() *config.Config {
	cfg := &config.Config{
		RobotID:         "test-robot",
		EVMPayeeAddress: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
		Price:           "$0.002",
		Network:         "eip155:84532",
		MPPEnabled:      true,
		MPPSecretKey:    testSecret,
	}
	if err := cfg.Validate(); err != nil {
		panic(err)
	}
	return cfg
}

// newTestRouter wires the gate in front of a stub x402 middleware and records
// whether that stub ran, which is how these tests observe the dispatch decision.
func newTestRouter(t *testing.T, cfg *config.Config) (*gin.Engine, *bool) {
	t.Helper()
	gin.SetMode(gin.TestMode)

	gate, err := New(cfg, zap.NewNop())
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if gate == nil {
		t.Fatal("expected a gate for an enabled config")
	}

	x402Ran := false
	x402 := func(c *gin.Context) {
		x402Ran = true
		c.Header("PAYMENT-REQUIRED", "stub")
		c.AbortWithStatusJSON(http.StatusPaymentRequired, gin.H{"error": "x402 payment required"})
	}

	router := gin.New()
	router.Use(gate.Middleware(x402))
	router.POST("/action", func(c *gin.Context) {
		body, _ := io.ReadAll(c.Request.Body)
		c.JSON(http.StatusOK, gin.H{"status": "accepted", "echo": string(body)})
	})
	router.GET("/health", func(c *gin.Context) { c.JSON(http.StatusOK, gin.H{"status": "ok"}) })

	return router, &x402Ran
}

func postAction(router *gin.Engine, headers map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"move"}`))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)
	return res
}

func TestNew_DisabledReturnsNoGate(t *testing.T) {
	cfg := testConfig()
	cfg.MPPEnabled = false

	gate, err := New(cfg, zap.NewNop())
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	if gate != nil {
		t.Fatal("expected no gate when MPP is disabled")
	}
}

// An unpaid request has to advertise both protocols, otherwise a payer holding
// only MPP support cannot tell that this robot would take it.
func TestMiddleware_UnpaidRequestAdvertisesBothProtocols(t *testing.T) {
	router, x402Ran := newTestRouter(t, testConfig())

	res := postAction(router, nil)

	if res.Code != http.StatusPaymentRequired {
		t.Fatalf("expected status 402, got %d", res.Code)
	}
	if !*x402Ran {
		t.Fatal("expected the x402 middleware to run for an unpaid request")
	}
	if got := res.Header().Get("PAYMENT-REQUIRED"); got == "" {
		t.Fatal("expected the x402 challenge header on the 402")
	}

	challenge := res.Header().Get("WWW-Authenticate")
	if challenge == "" {
		t.Fatal("expected an MPP challenge header on the 402")
	}
	if !strings.HasPrefix(challenge, "Payment ") {
		t.Fatalf("expected the Payment auth scheme, got %q", challenge)
	}
	for _, want := range []string{`realm="test-robot"`, `method="tempo"`, `intent="charge"`, "digest=", "expires="} {
		if !strings.Contains(challenge, want) {
			t.Fatalf("challenge %q is missing %q", challenge, want)
		}
	}
}

// An MPP credential means the payer chose MPP, so x402 must not also weigh in.
func TestMiddleware_MPPCredentialBypassesX402(t *testing.T) {
	router, x402Ran := newTestRouter(t, testConfig())

	res := postAction(router, map[string]string{"Authorization": "Payment credential=\"bm90LWEtY3JlZGVudGlhbA\""})

	if *x402Ran {
		t.Fatal("expected the x402 middleware to be skipped for an MPP credential")
	}
	if res.Code == http.StatusOK {
		t.Fatal("expected a bogus credential to be rejected")
	}
	if ct := res.Header().Get("Content-Type"); !strings.Contains(ct, "application/problem+json") {
		t.Fatalf("expected an RFC 9457 problem response, got Content-Type %q", ct)
	}
}

// A payer that already picked x402 should see the plain x402 flow, with no MPP
// challenge muddying the response.
func TestMiddleware_X402PaymentSkipsMPPChallenge(t *testing.T) {
	router, x402Ran := newTestRouter(t, testConfig())

	res := postAction(router, map[string]string{"PAYMENT-SIGNATURE": "stub-payment"})

	if !*x402Ran {
		t.Fatal("expected the x402 middleware to run")
	}
	if got := res.Header().Get("WWW-Authenticate"); got != "" {
		t.Fatalf("expected no MPP challenge alongside an x402 payment, got %q", got)
	}
}

// Non-Bearer/non-Payment Authorization values belong to whatever else is using
// the header (the AIP flow), so they must fall through untouched.
func TestMiddleware_NonPaymentAuthorizationFallsThrough(t *testing.T) {
	router, x402Ran := newTestRouter(t, testConfig())

	res := postAction(router, map[string]string{"Authorization": "Bearer some-token"})

	if !*x402Ran {
		t.Fatal("expected a Bearer token to fall through to x402")
	}
	if res.Header().Get("WWW-Authenticate") == "" {
		t.Fatal("expected the unpaid request to still advertise MPP")
	}
}

func TestMiddleware_UnpricedRouteIsNotCharged(t *testing.T) {
	router, x402Ran := newTestRouter(t, testConfig())

	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	res := httptest.NewRecorder()
	router.ServeHTTP(res, req)

	if !*x402Ran {
		t.Fatal("expected unpriced routes to be left to x402")
	}
	if res.Header().Get("WWW-Authenticate") != "" {
		t.Fatal("expected no MPP challenge on an unpriced route")
	}
}

// Issuing the challenge digests the body, so the body has to survive for the
// x402 middleware and the action handler behind it.
func TestMiddleware_ChallengeLeavesBodyReadable(t *testing.T) {
	cfg := testConfig()
	gin.SetMode(gin.TestMode)

	gate, err := New(cfg, zap.NewNop())
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	router := gin.New()
	router.Use(gate.Middleware(func(c *gin.Context) { c.Next() }))
	router.POST("/action", func(c *gin.Context) {
		body, err := io.ReadAll(c.Request.Body)
		if err != nil {
			t.Errorf("failed to read body in handler: %v", err)
		}
		c.String(http.StatusOK, string(body))
	})

	res := postAction(router, nil)

	if res.Body.String() != `{"action":"move"}` {
		t.Fatalf("expected the handler to see the original body, got %q", res.Body.String())
	}
}

// The advertised challenge is only useful if an MPP client can decode it, so
// parse it back with the SDK's own client-side parser and check the terms the
// payer would act on.
func TestMiddleware_AdvertisedChallengeIsClientParseable(t *testing.T) {
	router, _ := newTestRouter(t, testConfig())

	res := postAction(router, nil)

	challenge, err := mpp.ParseChallenge(res.Header().Get("WWW-Authenticate"))
	if err != nil {
		t.Fatalf("a client could not parse the challenge: %v", err)
	}

	if challenge.Method != "tempo" || challenge.Intent != "charge" {
		t.Fatalf("expected a tempo/charge challenge, got %s/%s", challenge.Method, challenge.Intent)
	}
	if challenge.Realm != "test-robot" {
		t.Errorf("expected realm %q, got %q", "test-robot", challenge.Realm)
	}

	request := challenge.Request
	// $0.002 at 6 decimals is 2000 atomic units of the chain's stablecoin.
	if request["amount"] != "2000" {
		t.Errorf("expected amount 2000, got %v", request["amount"])
	}
	if request["recipient"] != "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266" {
		t.Errorf("expected the configured payee, got %v", request["recipient"])
	}
	details, ok := request["methodDetails"].(map[string]any)
	if !ok {
		t.Fatalf("expected methodDetails in %v", request)
	}
	// Tempo mainnet, the default MPP network.
	if chainID, _ := details["chainId"].(float64); int64(chainID) != 4217 {
		t.Errorf("expected chain id 4217, got %v", details["chainId"])
	}
}
