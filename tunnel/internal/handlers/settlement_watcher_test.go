package handlers

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"

	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	"github.com/x402-foundation/x402/go/types"
	"go.uber.org/zap"
)

// fakeSettlementProcessor counts ProcessSettlement calls and returns a
// scripted result, so ExecutionWatcher can be tested without a real
// facilitator or on-chain settlement.
type fakeSettlementProcessor struct {
	calls  int
	result *x402http.ProcessSettleResult
}

func (f *fakeSettlementProcessor) ProcessSettlement(
	ctx context.Context,
	payload types.PaymentPayload,
	requirements types.PaymentRequirements,
	overrides *x402.SettlementOverrides,
	transportContext *x402http.HTTPTransportContext,
	declaredExtensions map[string]interface{},
) *x402http.ProcessSettleResult {
	f.calls++
	return f.result
}

func newTestWatcher(t *testing.T, settler *fakeSettlementProcessor) (*ExecutionWatcher, *IdempotencyStore) {
	t.Helper()
	storePath := filepath.Join(t.TempDir(), "idempotency_store.json")
	store, err := NewIdempotencyStore(storePath)
	if err != nil {
		t.Fatalf("failed to create idempotency store: %v", err)
	}
	return &ExecutionWatcher{
		Store:   store,
		Settler: settler,
		Logger:  zap.NewNop(),
	}, store
}

// Key fail-closed case for the async path (Go analogue of
// test_x402_no_settlement.py's tampered-signature scenario, applied to the
// execution side): a non-success terminal result must NEVER settle,
// regardless of what payment data was stored at accept-time.
func TestExecutionWatcher_NonSuccessResult_NeverSettles(t *testing.T) {
	settler := &fakeSettlementProcessor{result: &x402http.ProcessSettleResult{Success: true, Transaction: "0xshouldnotbecalled"}}
	watcher, store := newTestWatcher(t, settler)

	if _, _, err := store.Reserve("action-1"); err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if err := store.SetPaymentData("action-1", json.RawMessage(`{}`), json.RawMessage(`{}`)); err != nil {
		t.Fatalf("failed to seed payment data: %v", err)
	}

	watcher.HandleResult([]byte(`{"actionId":"action-1","status":"error"}`))

	if settler.calls != 0 {
		t.Fatalf("expected 0 ProcessSettlement calls for a non-success result, got %d", settler.calls)
	}
	status, ok := store.Get("action-1")
	if !ok {
		t.Fatalf("expected action-1 to still be present in the store")
	}
	if status.Settled {
		t.Fatalf("expected action-1 to remain unsettled after a non-success result")
	}
	if status.State != StateFailed {
		t.Fatalf("expected state %q, got %q", StateFailed, status.State)
	}
}

func TestExecutionWatcher_SuccessWithoutStoredPaymentData_NeverSettles(t *testing.T) {
	settler := &fakeSettlementProcessor{result: &x402http.ProcessSettleResult{Success: true}}
	watcher, store := newTestWatcher(t, settler)

	// Reserved but SetPaymentData was never called -- simulates a bug or
	// race where a result arrives before payment data was persisted.
	if _, _, err := store.Reserve("action-2"); err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}

	watcher.HandleResult([]byte(`{"actionId":"action-2","status":"success"}`))

	if settler.calls != 0 {
		t.Fatalf("expected 0 ProcessSettlement calls when no payment data was stored, got %d", settler.calls)
	}
	status, _ := store.Get("action-2")
	if status.State != StateSettlementFailed {
		t.Fatalf("expected state %q, got %q", StateSettlementFailed, status.State)
	}
	if status.Settled {
		t.Fatalf("expected action-2 to remain unsettled")
	}
}

func TestExecutionWatcher_SuccessWithPaymentData_SettlesExactlyOnce(t *testing.T) {
	settler := &fakeSettlementProcessor{result: &x402http.ProcessSettleResult{Success: true, Transaction: "0xabc"}}
	watcher, store := newTestWatcher(t, settler)

	if _, _, err := store.Reserve("action-3"); err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if err := store.SetPaymentData("action-3", json.RawMessage(`{}`), json.RawMessage(`{}`)); err != nil {
		t.Fatalf("failed to seed payment data: %v", err)
	}

	watcher.HandleResult([]byte(`{"actionId":"action-3","status":"success"}`))

	if settler.calls != 1 {
		t.Fatalf("expected exactly 1 ProcessSettlement call for a genuine success result, got %d", settler.calls)
	}
	status, _ := store.Get("action-3")
	if !status.Settled {
		t.Fatalf("expected action-3 to be marked settled")
	}
	if status.State != StateSucceeded {
		t.Fatalf("expected state %q, got %q", StateSucceeded, status.State)
	}
}

// Replays of a result that already settled must never trigger a second
// settlement -- this is the double-spend guard for the async path.
func TestExecutionWatcher_DuplicateSuccessResult_SettlesOnlyOnce(t *testing.T) {
	settler := &fakeSettlementProcessor{result: &x402http.ProcessSettleResult{Success: true, Transaction: "0xabc"}}
	watcher, store := newTestWatcher(t, settler)

	if _, _, err := store.Reserve("action-4"); err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if err := store.SetPaymentData("action-4", json.RawMessage(`{}`), json.RawMessage(`{}`)); err != nil {
		t.Fatalf("failed to seed payment data: %v", err)
	}

	watcher.HandleResult([]byte(`{"actionId":"action-4","status":"success"}`))
	watcher.HandleResult([]byte(`{"actionId":"action-4","status":"success"}`)) // replay

	if settler.calls != 1 {
		t.Fatalf("expected exactly 1 ProcessSettlement call across a replayed result, got %d", settler.calls)
	}
}

func TestExecutionWatcher_SettlementFailure_RecordsSettlementFailedState(t *testing.T) {
	settler := &fakeSettlementProcessor{result: &x402http.ProcessSettleResult{Success: false, ErrorReason: "INSUFFICIENT_FUNDS"}}
	watcher, store := newTestWatcher(t, settler)

	if _, _, err := store.Reserve("action-5"); err != nil {
		t.Fatalf("Reserve failed: %v", err)
	}
	if err := store.SetPaymentData("action-5", json.RawMessage(`{}`), json.RawMessage(`{}`)); err != nil {
		t.Fatalf("failed to seed payment data: %v", err)
	}

	watcher.HandleResult([]byte(`{"actionId":"action-5","status":"success"}`))

	if settler.calls != 1 {
		t.Fatalf("expected exactly 1 ProcessSettlement attempt, got %d", settler.calls)
	}
	status, _ := store.Get("action-5")
	if status.Settled {
		t.Fatalf("expected action-5 to remain unsettled after a failed settlement attempt")
	}
	if status.State != StateSettlementFailed {
		t.Fatalf("expected state %q, got %q", StateSettlementFailed, status.State)
	}
}

func TestExecutionWatcher_UnknownActionID_DoesNotSettle(t *testing.T) {
	settler := &fakeSettlementProcessor{result: &x402http.ProcessSettleResult{Success: true}}
	watcher, _ := newTestWatcher(t, settler)

	watcher.HandleResult([]byte(`{"actionId":"never-reserved","status":"success"}`))

	if settler.calls != 0 {
		t.Fatalf("expected 0 ProcessSettlement calls for an unknown actionId, got %d", settler.calls)
	}
}
