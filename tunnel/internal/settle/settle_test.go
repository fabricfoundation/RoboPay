package settle

import (
	"context"
	"testing"
	"time"

	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/internal/ledger"
)

// fakeFacilitator lets tests control Settle's outcome per call without
// hitting a real facilitator.
type fakeFacilitator struct {
	response *SettleResponse
	err      error
	calls    int
}

func (f *fakeFacilitator) Settle(ctx context.Context, payloadBytes, requirementsBytes []byte) (*SettleResponse, error) {
	f.calls++
	if f.err != nil {
		return nil, f.err
	}
	return f.response, nil
}

func newSucceededEntry(t *testing.T, ldg *ledger.Ledger, actionID string, withPayment bool) {
	t.Helper()
	entry, err := ldg.Reserve(actionID, "robot-1", "look_at_apple", "idem-"+actionID, "hash-1")
	if err != nil {
		t.Fatalf("reserve: %v", err)
	}
	if withPayment {
		if err := ldg.AttachPayment(entry.ActionID, map[string]interface{}{"p": "payload"}, map[string]interface{}{"r": "requirements"}); err != nil {
			t.Fatalf("attach payment: %v", err)
		}
	}
	if err := ldg.MarkSuccess(entry.ActionID, "done"); err != nil {
		t.Fatalf("mark success: %v", err)
	}
}

func TestSweepOnce_SettlesSuccessfulActionWithPayment(t *testing.T) {
	ldg := ledger.New()
	newSucceededEntry(t, ldg, "action-1", true)

	fac := &fakeFacilitator{response: &SettleResponse{Success: true, Transaction: "0xabc", Network: "eip155:8453"}}
	w := New(ldg, fac, zap.NewNop(), time.Second)

	w.sweepOnce(context.Background())

	entry, err := ldg.Get("action-1")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if !entry.Settled {
		t.Fatalf("expected entry to be settled")
	}
	if entry.SettleTx != "0xabc" || entry.SettleNetwork != "eip155:8453" {
		t.Fatalf("unexpected settle tx/network: %+v", entry)
	}
	if fac.calls != 1 {
		t.Fatalf("expected 1 facilitator call, got %d", fac.calls)
	}
}

func TestSweepOnce_NoPaymentAttachedMarksSettledWithoutCallingFacilitator(t *testing.T) {
	ldg := ledger.New()
	newSucceededEntry(t, ldg, "action-free", false)

	fac := &fakeFacilitator{response: &SettleResponse{Success: true}}
	w := New(ldg, fac, zap.NewNop(), time.Second)

	w.sweepOnce(context.Background())

	entry, err := ldg.Get("action-free")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if !entry.Settled {
		t.Fatalf("expected free action to be marked settled")
	}
	if fac.calls != 0 {
		t.Fatalf("expected 0 facilitator calls for a no-payment action, got %d", fac.calls)
	}
}

func TestSweepOnce_FacilitatorFailureLeavesActionUnsettledForRetry(t *testing.T) {
	ldg := ledger.New()
	newSucceededEntry(t, ldg, "action-fail", true)

	fac := &fakeFacilitator{response: &SettleResponse{Success: false, ErrorReason: "insufficient_funds"}}
	w := New(ldg, fac, zap.NewNop(), time.Second)

	w.sweepOnce(context.Background())

	entry, err := ldg.Get("action-fail")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if entry.Settled {
		t.Fatalf("expected entry to remain unsettled after facilitator failure")
	}

	ids := ldg.UnsettledSuccessActionIDs()
	if len(ids) != 1 || ids[0] != "action-fail" {
		t.Fatalf("expected action-fail to still be swept next round, got %v", ids)
	}
}

func TestSweepOnce_NeverSettlesFailedAction(t *testing.T) {
	ldg := ledger.New()
	entry, err := ldg.Reserve("action-bad", "robot-1", "look_at_apple", "idem-bad", "hash-1")
	if err != nil {
		t.Fatalf("reserve: %v", err)
	}
	if err := ldg.MarkFailed(entry.ActionID, "EXEC_ERROR", "robot fault"); err != nil {
		t.Fatalf("mark failed: %v", err)
	}

	fac := &fakeFacilitator{response: &SettleResponse{Success: true, Transaction: "0xshouldnothappen"}}
	w := New(ldg, fac, zap.NewNop(), time.Second)

	w.sweepOnce(context.Background())

	if fac.calls != 0 {
		t.Fatalf("expected facilitator to never be called for a failed action, got %d calls", fac.calls)
	}
	got, err := ldg.Get("action-bad")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got.Settled {
		t.Fatalf("failed action must never be marked settled")
	}
}

func TestRun_StopsOnContextCancel(t *testing.T) {
	ldg := ledger.New()
	fac := &fakeFacilitator{response: &SettleResponse{Success: true}}
	w := New(ldg, fac, zap.NewNop(), 10*time.Millisecond)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		w.Run(ctx)
		close(done)
	}()

	time.Sleep(30 * time.Millisecond)
	cancel()

	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatalf("Run did not return after context cancellation")
	}
}
