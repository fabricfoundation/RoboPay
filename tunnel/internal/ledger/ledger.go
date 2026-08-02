// Package ledger tracks the lifecycle of a paid action from acceptance
// through to a terminal result, keyed by actionId. It is the single
// source of truth the tunnel consults before ever calling Settle, and
// the source the status endpoint reads from.
//
// Design choice: in-memory map guarded by a mutex, not a file-backed
// store. Status reads happen on every polling request and must be fast;
// durability across process restarts is handled separately by
// periodically snapshotting to disk (see SnapshotTo/LoadFrom), rather
// than paying disk I/O on every write.
package ledger

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

// State is the lifecycle stage of a tracked action.
type State string

const (
	StatePending State = "pending"
	StateSuccess State = "succeeded"
	StateFailed  State = "failed"
	StateTimeout State = "timeout"
)

// Entry is everything the ledger keeps about one action.
type Entry struct {
	ActionID       string          `json:"action_id"`
	RobotID        string          `json:"robot_id"`
	SkillID        string          `json:"skill_id"`
	IdempotencyKey string          `json:"idempotency_key"`
	ParamsHash     string          `json:"params_hash"`
	State          State           `json:"state"`
	CreatedAt      time.Time       `json:"created_at"`
	UpdatedAt      time.Time       `json:"updated_at"`
	ResultMessage  string          `json:"result_message,omitempty"`
	ErrorCode      string          `json:"error_code,omitempty"`
	ErrorMessage   string          `json:"error_message,omitempty"`
	Settled        bool            `json:"settled"`
	SettleTx       string          `json:"settle_tx,omitempty"`
	SettleNetwork  string          `json:"settle_network,omitempty"`

	// Payment payload/requirements are kept only long enough for the
	// settle watcher to use them once execution succeeds; they are not
	// serialized in status responses (see handlers layer for that).
	paymentPayload      interface{} `json:"-"`
	paymentRequirements interface{} `json:"-"`
}

// ErrDuplicate is returned by Reserve when the idempotency key has
// already been reserved -- the caller must treat this as a replay.
var ErrDuplicate = fmt.Errorf("idempotency key already reserved")

// ErrNotFound is returned when looking up an unknown actionId.
var ErrNotFound = fmt.Errorf("action not found")

type Ledger struct {
	mu       sync.RWMutex
	byAction map[string]*Entry
	byIdem   map[string]string // idempotencyKey -> actionId, for replay checks
}

func New() *Ledger {
	return &Ledger{
		byAction: make(map[string]*Entry),
		byIdem:   make(map[string]string),
	}
}

// Reserve atomically checks the idempotency key and, if unused, creates
// a new pending Entry for actionId. Returns ErrDuplicate if the key was
// already reserved (regardless of that action's current state) -- a
// replay must never cause a second actuation, even if the original
// request already finished.
func (l *Ledger) Reserve(actionID, robotID, skillID, idempotencyKey, paramsHash string) (*Entry, error) {
	l.mu.Lock()
	defer l.mu.Unlock()

	if existingID, ok := l.byIdem[idempotencyKey]; ok {
		_ = existingID
		return nil, ErrDuplicate
	}

	now := time.Now().UTC()
	entry := &Entry{
		ActionID:       actionID,
		RobotID:        robotID,
		SkillID:        skillID,
		IdempotencyKey: idempotencyKey,
		ParamsHash:     paramsHash,
		State:          StatePending,
		CreatedAt:      now,
		UpdatedAt:      now,
	}

	l.byAction[actionID] = entry
	l.byIdem[idempotencyKey] = actionID
	return entry, nil
}

// AttachPayment stores the payment payload/requirements needed for a
// deferred Settle call once execution succeeds. Kept separate from
// Reserve so payment context can be attached after verification.
func (l *Ledger) AttachPayment(actionID string, payload, requirements interface{}) error {
	l.mu.Lock()
	defer l.mu.Unlock()

	entry, ok := l.byAction[actionID]
	if !ok {
		return ErrNotFound
	}
	entry.paymentPayload = payload
	entry.paymentRequirements = requirements
	return nil
}

// Payment returns the stored payment payload/requirements for an action,
// used by the settle watcher immediately before calling ProcessSettlement.
func (l *Ledger) Payment(actionID string) (payload, requirements interface{}, err error) {
	l.mu.RLock()
	defer l.mu.RUnlock()

	entry, ok := l.byAction[actionID]
	if !ok {
		return nil, nil, ErrNotFound
	}
	return entry.paymentPayload, entry.paymentRequirements, nil
}

// MarkSuccess transitions an action to succeeded with a result message.
// It does not perform settlement itself -- that is the settle watcher's
// job, kept separate so the ledger has no payment/network dependencies.
func (l *Ledger) MarkSuccess(actionID, resultMessage string) error {
	l.mu.Lock()
	defer l.mu.Unlock()

	entry, ok := l.byAction[actionID]
	if !ok {
		return ErrNotFound
	}
	entry.State = StateSuccess
	entry.ResultMessage = resultMessage
	entry.UpdatedAt = time.Now().UTC()
	return nil
}

// MarkFailed transitions an action to failed with an error code/message.
// A failed action must never be settled -- callers of Settled/MarkSettled
// are expected to check State first.
func (l *Ledger) MarkFailed(actionID, errorCode, errorMessage string) error {
	l.mu.Lock()
	defer l.mu.Unlock()

	entry, ok := l.byAction[actionID]
	if !ok {
		return ErrNotFound
	}
	entry.State = StateFailed
	entry.ErrorCode = errorCode
	entry.ErrorMessage = errorMessage
	entry.UpdatedAt = time.Now().UTC()
	return nil
}

// MarkTimeout transitions a still-pending action to timeout. No-op if
// the action already reached a terminal state (a late result arriving
// after timeout must not overwrite it).
func (l *Ledger) MarkTimeout(actionID string) error {
	l.mu.Lock()
	defer l.mu.Unlock()

	entry, ok := l.byAction[actionID]
	if !ok {
		return ErrNotFound
	}
	if entry.State != StatePending {
		return nil
	}
	entry.State = StateTimeout
	entry.ErrorCode = "TIMEOUT"
	entry.ErrorMessage = "action did not complete within the allotted time"
	entry.UpdatedAt = time.Now().UTC()
	return nil
}

// MarkSettled records a completed settlement. Callers must only call
// this after State == StateSuccess; the ledger itself does not enforce
// that ordering since it has no opinion on payment logic, but the
// settle watcher must never call this otherwise.
func (l *Ledger) MarkSettled(actionID, tx, network string) error {
	l.mu.Lock()
	defer l.mu.Unlock()

	entry, ok := l.byAction[actionID]
	if !ok {
		return ErrNotFound
	}
	entry.Settled = true
	entry.SettleTx = tx
	entry.SettleNetwork = network
	entry.UpdatedAt = time.Now().UTC()
	return nil
}

// Get returns a copy of the entry for actionID (safe to read/serialize
// without holding the ledger's lock further).
func (l *Ledger) Get(actionID string) (Entry, error) {
	l.mu.RLock()
	defer l.mu.RUnlock()

	entry, ok := l.byAction[actionID]
	if !ok {
		return Entry{}, ErrNotFound
	}
	return *entry, nil
}

// PendingActionIDs returns all actionIds still in StatePending, used by
// the timeout sweeper.
func (l *Ledger) PendingActionIDs() []string {
	l.mu.RLock()
	defer l.mu.RUnlock()

	ids := make([]string, 0)
	for id, entry := range l.byAction {
		if entry.State == StatePending {
			ids = append(ids, id)
		}
	}
	return ids
}

// UnsettledSuccessActionIDs returns actionIds that finished successfully
// but have not yet been settled -- the set the settle watcher must sweep.
// Only StateSuccess entries qualify; StateFailed/StateTimeout must never
// appear here, enforcing no-settle-on-failure at the ledger's own boundary.
func (l *Ledger) UnsettledSuccessActionIDs() []string {
	l.mu.RLock()
	defer l.mu.RUnlock()

	ids := make([]string, 0)
	for id, entry := range l.byAction {
		if entry.State == StateSuccess && !entry.Settled {
			ids = append(ids, id)
		}
	}
	return ids
}

// snapshotEntry is the subset of Entry that survives a restart -- the
// in-memory-only payment fields are deliberately excluded so a crash
// mid-flight never leaves a payment payload sitting on disk.
type snapshotEntry struct {
	Entry
}

// SnapshotTo writes the current ledger state to path as JSON. Intended
// to be called periodically (e.g. every few seconds) by the caller, not
// on every mutation -- see the package doc for the rationale.
func (l *Ledger) SnapshotTo(path string) error {
	l.mu.RLock()
	entries := make([]snapshotEntry, 0, len(l.byAction))
	for _, e := range l.byAction {
		entries = append(entries, snapshotEntry{Entry: *e})
	}
	l.mu.RUnlock()

	data, err := json.MarshalIndent(entries, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal snapshot: %w", err)
	}

	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return fmt.Errorf("write snapshot: %w", err)
	}
	return os.Rename(tmp, path)
}

// LoadFrom restores ledger state from a snapshot written by SnapshotTo.
// Any action still StatePending at snapshot time is loaded as
// StateTimeout instead -- a pending action that didn't finish before a
// restart cannot be trusted to still be in flight, and must never be
// eligible for settlement after recovery.
func (l *Ledger) LoadFrom(path string) error {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read snapshot: %w", err)
	}

	var entries []snapshotEntry
	if err := json.Unmarshal(data, &entries); err != nil {
		return fmt.Errorf("unmarshal snapshot: %w", err)
	}

	l.mu.Lock()
	defer l.mu.Unlock()

	for _, se := range entries {
		e := se.Entry
		if e.State == StatePending {
			e.State = StateTimeout
			e.ErrorCode = "RESTART_INTERRUPTED"
			e.ErrorMessage = "tunnel restarted before this action completed"
		}
		entryCopy := e
		l.byAction[e.ActionID] = &entryCopy
		l.byIdem[e.IdempotencyKey] = e.ActionID
	}
	return nil
}
