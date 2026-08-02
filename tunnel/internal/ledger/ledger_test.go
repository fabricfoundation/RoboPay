package ledger

import (
	"os"
	"path/filepath"
	"testing"
)

func TestReserve_FirstTimeSucceeds(t *testing.T) {
	l := New()
	entry, err := l.Reserve("act1", "reachy-mini", "look_at_apple", "idem1", "hash1")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if entry.State != StatePending {
		t.Fatalf("expected pending, got %s", entry.State)
	}
}

func TestReserve_DuplicateIdempotencyKeyRejected(t *testing.T) {
	l := New()
	if _, err := l.Reserve("act1", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != nil {
		t.Fatalf("first reserve failed: %v", err)
	}
	if _, err := l.Reserve("act2", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != ErrDuplicate {
		t.Fatalf("expected ErrDuplicate, got %v", err)
	}
}

func TestReserve_DuplicateRejectedEvenAfterTerminalState(t *testing.T) {
	// A replay must never cause a second actuation, even if the
	// original action already finished successfully.
	l := New()
	if _, err := l.Reserve("act1", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != nil {
		t.Fatalf("reserve failed: %v", err)
	}
	if err := l.MarkSuccess("act1", "tracked apple"); err != nil {
		t.Fatalf("mark success failed: %v", err)
	}
	if _, err := l.Reserve("act2", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != ErrDuplicate {
		t.Fatalf("expected ErrDuplicate after terminal state, got %v", err)
	}
}

func TestMarkFailed_NeverMarkedSettled(t *testing.T) {
	l := New()
	if _, err := l.Reserve("act1", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != nil {
		t.Fatalf("reserve failed: %v", err)
	}
	if err := l.MarkFailed("act1", "SIM_FAILURE", "simulator crashed"); err != nil {
		t.Fatalf("mark failed failed: %v", err)
	}
	entry, err := l.Get("act1")
	if err != nil {
		t.Fatalf("get failed: %v", err)
	}
	if entry.State != StateFailed {
		t.Fatalf("expected failed, got %s", entry.State)
	}
	if entry.Settled {
		t.Fatalf("a failed action must never be marked settled")
	}
}

func TestMarkTimeout_NoOpOnTerminalState(t *testing.T) {
	l := New()
	if _, err := l.Reserve("act1", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != nil {
		t.Fatalf("reserve failed: %v", err)
	}
	if err := l.MarkSuccess("act1", "done"); err != nil {
		t.Fatalf("mark success failed: %v", err)
	}
	if err := l.MarkTimeout("act1"); err != nil {
		t.Fatalf("mark timeout failed: %v", err)
	}
	entry, _ := l.Get("act1")
	if entry.State != StateSuccess {
		t.Fatalf("a late timeout must not override an already-successful action, got %s", entry.State)
	}
}

func TestGet_UnknownActionReturnsNotFound(t *testing.T) {
	l := New()
	if _, err := l.Get("nope"); err != ErrNotFound {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}

func TestSnapshotRoundTrip(t *testing.T) {
	l := New()
	if _, err := l.Reserve("act1", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != nil {
		t.Fatalf("reserve failed: %v", err)
	}
	if err := l.MarkSuccess("act1", "tracked apple"); err != nil {
		t.Fatalf("mark success failed: %v", err)
	}

	dir := t.TempDir()
	path := filepath.Join(dir, "ledger.json")
	if err := l.SnapshotTo(path); err != nil {
		t.Fatalf("snapshot failed: %v", err)
	}

	l2 := New()
	if err := l2.LoadFrom(path); err != nil {
		t.Fatalf("load failed: %v", err)
	}
	entry, err := l2.Get("act1")
	if err != nil {
		t.Fatalf("get after load failed: %v", err)
	}
	if entry.State != StateSuccess {
		t.Fatalf("expected succeeded to survive snapshot round-trip, got %s", entry.State)
	}
}

func TestSnapshotRoundTrip_PendingBecomesTimeoutAfterRestart(t *testing.T) {
	// A pending action interrupted by a restart cannot be trusted to
	// still be in flight -- it must never remain eligible for
	// settlement after recovery.
	l := New()
	if _, err := l.Reserve("act1", "reachy-mini", "look_at_apple", "idem1", "hash1"); err != nil {
		t.Fatalf("reserve failed: %v", err)
	}

	dir := t.TempDir()
	path := filepath.Join(dir, "ledger.json")
	if err := l.SnapshotTo(path); err != nil {
		t.Fatalf("snapshot failed: %v", err)
	}

	l2 := New()
	if err := l2.LoadFrom(path); err != nil {
		t.Fatalf("load failed: %v", err)
	}
	entry, _ := l2.Get("act1")
	if entry.State != StateTimeout {
		t.Fatalf("expected pending-at-snapshot to become timeout after restart, got %s", entry.State)
	}
}

func TestLoadFrom_MissingFileIsNotAnError(t *testing.T) {
	l := New()
	err := l.LoadFrom(filepath.Join(os.TempDir(), "does-not-exist-ledger.json"))
	if err != nil {
		t.Fatalf("expected no error for missing snapshot file, got %v", err)
	}
}
