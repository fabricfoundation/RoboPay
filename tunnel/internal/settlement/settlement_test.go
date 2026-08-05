package settlement

import (
	"testing"
)

func TestProcessResultSettlesOnlyOnSuccess(t *testing.T) {
	mgr := NewSettlementManager()

	result := ResultEnvelope{
		ActionID: "action-1",
		Status:   "error",
		Code:     "ACTION_FAILED",
	}
	mgr.ProcessResult(result)

	if mgr.IsSettled("action-1") {
		t.Fatal("expected action-1 not to be settled on error result")
	}

	success := ResultEnvelope{
		ActionID: "action-2",
		Status:   "success",
	}
	mgr.ProcessResult(success)

	if !mgr.IsSettled("action-2") {
		t.Fatal("expected action-2 to be settled on success result")
	}
}

func TestGetResultReturnsStoredTerminalResult(t *testing.T) {
	mgr := NewSettlementManager()

	result := ResultEnvelope{
		ActionID: "action-3",
		Status:   "success",
		Message:  "ok",
	}
	mgr.ProcessResult(result)

	stored, ok := mgr.GetResult("action-3")
	if !ok {
		t.Fatal("expected result to be present")
	}
	if stored.Message != "ok" {
		t.Fatalf("expected stored message to be ok, got %q", stored.Message)
	}
}
