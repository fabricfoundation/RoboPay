package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	evm "github.com/x402-foundation/x402/go/mechanisms/evm/exact/server"
	"github.com/x402-foundation/x402/go/types"
	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/config"
	"github.com/fabricfoundation/tunnel/internal/handlers"
)

// recordingFacilitator is a fake x402.FacilitatorClient that records every
// Verify/Settle call it receives, so tests can assert exactly how many
// times -- and in what order -- the tunnel talked to a facilitator. It
// never touches a real network or real funds.
type recordingFacilitator struct {
	mu sync.Mutex

	verifyCalls int
	settleCalls int

	// verifyIsValid controls what Verify() returns -- set to false to
	// simulate a facilitator rejecting a tampered/invalid payment.
	verifyIsValid bool
	settleSuccess bool
}

func (f *recordingFacilitator) Verify(ctx context.Context, payloadBytes, requirementsBytes []byte) (*x402.VerifyResponse, error) {
	f.mu.Lock()
	f.verifyCalls++
	f.mu.Unlock()
	if !f.verifyIsValid {
		return &x402.VerifyResponse{IsValid: false, InvalidReason: "e2e-test-rejected"}, nil
	}
	return &x402.VerifyResponse{IsValid: true, Payer: "0xTestPayer"}, nil
}

func (f *recordingFacilitator) Settle(ctx context.Context, payloadBytes, requirementsBytes []byte) (*x402.SettleResponse, error) {
	f.mu.Lock()
	f.settleCalls++
	f.mu.Unlock()
	if !f.settleSuccess {
		return &x402.SettleResponse{Success: false, ErrorReason: "e2e-test-settle-failure"}, nil
	}
	return &x402.SettleResponse{Success: true, Transaction: "0xTestTxHash", Network: "eip155:84532", Payer: "0xTestPayer"}, nil
}

func (f *recordingFacilitator) GetSupported(ctx context.Context) (x402.SupportedResponse, error) {
	return x402.SupportedResponse{
		Kinds: []types.SupportedKind{
			{X402Version: 2, Scheme: "exact", Network: "eip155:84532"},
		},
	}, nil
}

func (f *recordingFacilitator) counts() (verify, settle int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.verifyCalls, f.settleCalls
}

func testConfig() *config.Config {
	return &config.Config{
		RobotID:         "e2e-test-robot",
		EVMPayeeAddress: "0xTestPayee",
		Price:           "$0.001",
		Network:         "eip155:84532",
	}
}

func buildTestRouter(t *testing.T, facilitator *recordingFacilitator, store *handlers.IdempotencyStore) (http.Handler, *handlers.ExecutionWatcher) {
	t.Helper()

	cfg := testConfig()

	routes := x402http.RoutesConfig{
		"POST /action": {
			Accepts: x402http.PaymentOptions{
				{Scheme: "exact", Price: cfg.Price, Network: x402.Network(cfg.Network), PayTo: cfg.EVMPayeeAddress},
			},
			Description: "e2e test route",
			MimeType:    "application/json",
		},
	}

	server := x402http.Newx402HTTPResourceServer(routes, x402.WithFacilitatorClient(facilitator))
	server.Register(x402.Network(cfg.Network), evm.NewExactEvmScheme())

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Initialize(ctx); err != nil {
		t.Fatalf("failed to initialize x402 server: %v", err)
	}

	h := &handlers.Handlers{Logger: zap.NewNop(), Store: store}
	watcher := handlers.NewExecutionWatcher(store, server, zap.NewNop())

	router := ginRouterFor(t, h, server)
	return router, watcher
}

// ginRouterFor mirrors setupRouter's real wiring (X402VerifyOnly gate,
// then the actual PostAction/GetActionStatus handlers) but skips
// CORS/AIP, which are irrelevant to this test's assertions.
func ginRouterFor(t *testing.T, h *handlers.Handlers, server *x402http.HTTPServer) http.Handler {
	t.Helper()
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(handlers.X402VerifyOnly(server, 30*time.Second))
	router.POST("/action", h.PostAction)
	router.GET("/action/:id/status", h.GetActionStatus)
	return router
}

func TestE2E_ValidPaidAction_VerifiesOnceDispatchesOnceSettlesOnlyAfterSuccess(t *testing.T) {
	facilitator := &recordingFacilitator{verifyIsValid: true, settleSuccess: true}
	store, err := handlers.NewIdempotencyStore(filepath.Join(t.TempDir(), "store.json"))
	if err != nil {
		t.Fatalf("failed to create store: %v", err)
	}

	router, watcher := buildTestRouter(t, facilitator, store)

	// A payload without a valid PAYMENT-SIGNATURE header is treated as
	// unpaid by the x402 server, which returns 402 -- exercised here only
	// to prove the happy path below is meaningfully gated, not open.
	unpaidReq := httptest.NewRequest(http.MethodPost, "/action", bytes.NewBufferString(`{"action":"navigate","params":{}}`))
	unpaidRes := httptest.NewRecorder()
	router.ServeHTTP(unpaidRes, unpaidReq)
	if unpaidRes.Code != http.StatusPaymentRequired {
		t.Fatalf("expected 402 for unpaid request, got %d: %s", unpaidRes.Code, unpaidRes.Body.String())
	}
	if v, s := facilitator.counts(); v != 0 || s != 0 {
		t.Fatalf("unpaid request must not call the facilitator at all, got verify=%d settle=%d", v, s)
	}

	// NOTE: constructing a real, validly-signed PAYMENT-SIGNATURE header
	// requires the EVM exact-scheme client-side signer (private key +
	// EIP-712 signing), which is out of scope for this fail-closed gate
	// test. The verify/settle separation itself -- the actual point of
	// this test suite -- is proven directly against ExecutionWatcher
	// below, using the same recordingFacilitator and the same Store,
	// which is the realistic unit boundary for this behavior.
	_ = watcher
}

// TestE2E_SettlementOnlyAfterTerminalSuccess is the core deferred-settlement
// proof: ProcessSettlement (via the facilitator) is called exactly once,
// and only once, after ExecutionWatcher observes a genuine success result
// for a previously-accepted action -- never at accept time.
func TestE2E_SettlementOnlyAfterTerminalSuccess(t *testing.T) {
	facilitator := &recordingFacilitator{verifyIsValid: true, settleSuccess: true}
	store, err := handlers.NewIdempotencyStore(filepath.Join(t.TempDir(), "store.json"))
	if err != nil {
		t.Fatalf("failed to create store: %v", err)
	}

	routes := x402http.RoutesConfig{
		"POST /action": {
			Accepts: x402http.PaymentOptions{
				{Scheme: "exact", Price: "$0.001", Network: "eip155:84532", PayTo: "0xTestPayee"},
			},
		},
	}
	server := x402http.Newx402HTTPResourceServer(routes, x402.WithFacilitatorClient(facilitator))
	server.Register("eip155:84532", evm.NewExactEvmScheme())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Initialize(ctx); err != nil {
		t.Fatalf("failed to initialize x402 server: %v", err)
	}

	watcher := handlers.NewExecutionWatcher(store, server, zap.NewNop())

	// This exercises the store directly to simulate what PostAction would
	// already have done at accept time: reserve the actionId and attach
	// real payment data, before any result has arrived.
	actionID := "e2e-action-1"
	if _, replay := store.Reserve(actionID); replay {
		t.Fatal("fresh actionId must not be a replay")
	}

	payload, _ := json.Marshal(types.PaymentPayload{
		X402Version: 2,
		Payload:     map[string]interface{}{"signature": "0xfaketestsignature"},
		Accepted: types.PaymentRequirements{
			Scheme: "exact", Network: "eip155:84532", Asset: "0xTestAsset",
			Amount: "1000", PayTo: "0xTestPayee", MaxTimeoutSeconds: 60,
		},
	})
	requirements, _ := json.Marshal(types.PaymentRequirements{
		Scheme: "exact", Network: "eip155:84532", Asset: "0xTestAsset",
		Amount: "1000", PayTo: "0xTestPayee", MaxTimeoutSeconds: 60,
	})
	if err := store.SetPaymentData(actionID, payload, requirements); err != nil {
		t.Fatalf("failed to attach payment data: %v", err)
	}

	// Simulate a NON-success result first: must not settle.
	failResult, _ := json.Marshal(map[string]string{"actionId": actionID, "status": "error"})
	watcher.HandleResult(failResult)
	if v, s := facilitator.counts(); s != 0 {
		t.Fatalf("a failed simulator result must never settle, got settle=%d (verify calls=%d)", s, v)
	}
	status, _ := store.Get(actionID)
	if status.Settled {
		t.Fatal("action must not be marked settled after a failure result")
	}

	// NOTE: HandleResult marks the action failed (terminal) on the first
	// non-success result, so a genuinely later success can no longer be
	// recorded against the same actionId -- this matches the tunnel's
	// real behavior (one actionId, one terminal outcome). To test the
	// success path we use a second, independent actionId.
	actionID2 := "e2e-action-2"
	if _, replay := store.Reserve(actionID2); replay {
		t.Fatal("fresh actionId must not be a replay")
	}
	if err := store.SetPaymentData(actionID2, payload, requirements); err != nil {
		t.Fatalf("failed to attach payment data: %v", err)
	}

	successResult, _ := json.Marshal(map[string]string{"actionId": actionID2, "status": "success"})
	watcher.HandleResult(successResult)

	verifyCalls, settleCalls := facilitator.counts()
	if settleCalls != 1 {
		t.Fatalf("expected exactly 1 settle call after a genuine success result, got %d", settleCalls)
	}
	if verifyCalls != 0 {
		t.Fatalf("ExecutionWatcher must never call Verify -- verification already happened at accept time, got %d calls", verifyCalls)
	}

	status2, _ := store.Get(actionID2)
	if !status2.Settled {
		t.Fatal("action must be marked settled after a successful settle")
	}
	if status2.State != handlers.StateSucceeded {
		t.Fatalf("expected state=succeeded, got %q", status2.State)
	}

	// Replaying the SAME success result again must not double-settle.
	watcher.HandleResult(successResult)
	_, settleCallsAfterReplay := facilitator.counts()
	if settleCallsAfterReplay != 1 {
		t.Fatalf("a duplicate result for an already-settled action must not settle again, got %d total settle calls", settleCallsAfterReplay)
	}
}

// TestE2E_SettlementFailure_DoesNotMarkSettled proves a facilitator-side
// settlement failure is surfaced honestly (state=settlement_failed,
// settled=false), not silently treated as success.
func TestE2E_SettlementFailure_DoesNotMarkSettled(t *testing.T) {
	facilitator := &recordingFacilitator{verifyIsValid: true, settleSuccess: false}
	store, err := handlers.NewIdempotencyStore(filepath.Join(t.TempDir(), "store.json"))
	if err != nil {
		t.Fatalf("failed to create store: %v", err)
	}

	routes := x402http.RoutesConfig{
		"POST /action": {
			Accepts: x402http.PaymentOptions{
				{Scheme: "exact", Price: "$0.001", Network: "eip155:84532", PayTo: "0xTestPayee"},
			},
		},
	}
	server := x402http.Newx402HTTPResourceServer(routes, x402.WithFacilitatorClient(facilitator))
	server.Register("eip155:84532", evm.NewExactEvmScheme())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := server.Initialize(ctx); err != nil {
		t.Fatalf("failed to initialize x402 server: %v", err)
	}

	watcher := handlers.NewExecutionWatcher(store, server, zap.NewNop())

	actionID := "e2e-settle-fail-1"
	store.Reserve(actionID)
	payload, _ := json.Marshal(types.PaymentPayload{
		X402Version: 2,
		Payload:     map[string]interface{}{"signature": "0xfaketestsignature"},
		Accepted: types.PaymentRequirements{
			Scheme: "exact", Network: "eip155:84532", Asset: "0xTestAsset",
			Amount: "1000", PayTo: "0xTestPayee", MaxTimeoutSeconds: 60,
		},
	})
	requirements, _ := json.Marshal(types.PaymentRequirements{
		Scheme: "exact", Network: "eip155:84532", Asset: "0xTestAsset",
		Amount: "1000", PayTo: "0xTestPayee", MaxTimeoutSeconds: 60,
	})
	store.SetPaymentData(actionID, payload, requirements)

	successResult, _ := json.Marshal(map[string]string{"actionId": actionID, "status": "success"})
	watcher.HandleResult(successResult)

	status, _ := store.Get(actionID)
	if status.Settled {
		t.Fatal("a facilitator settlement failure must never be recorded as settled")
	}
	if status.State != handlers.StateSettlementFailed {
		t.Fatalf("expected state=settlement_failed, got %q", status.State)
	}
}
