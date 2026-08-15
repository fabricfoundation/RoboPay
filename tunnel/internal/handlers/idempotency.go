package handlers

import (
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"
)

// ActionStatus is the durable record for one action, keyed by actionId.
// It survives tunnel restarts by being persisted to a JSON file on every
// state transition, so a replayed request or a status poll after a crash
// still sees the correct terminal state.
type ActionStatus struct {
	ActionID  string    `json:"action_id"`
	State     string    `json:"state"` // pending | succeeded | failed | timeout | settlement_failed
	Settled   bool      `json:"settled"`
	ErrorCode string    `json:"error_code,omitempty"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`

	// PaymentPayload and PaymentRequirements are the verified x402 payment
	// data captured at accept-time, stored raw (not settled yet). They are
	// consumed exactly once by the execution watcher when a terminal
	// success result arrives, and never used for settlement before that.
	PaymentPayload      json.RawMessage `json:"payment_payload,omitempty"`
	PaymentRequirements json.RawMessage `json:"payment_requirements,omitempty"`
}

const (
	StatePending          = "pending"
	StateSucceeded        = "succeeded"
	StateFailed           = "failed"
	StateTimeout          = "timeout"
	StateSettlementFailed = "settlement_failed"
)

// IdempotencyStore is a file-backed, mutex-guarded map of actionId ->
// ActionStatus. One JSON file per store (path from IDEMPOTENCY_STORE_PATH,
// defaulting to idempotency_store.json in the working directory).
type IdempotencyStore struct {
	mu   sync.Mutex
	path string
	data map[string]*ActionStatus
}

func NewIdempotencyStore(path string) (*IdempotencyStore, error) {
	if path == "" {
		path = "idempotency_store.json"
	}
	s := &IdempotencyStore{path: path, data: make(map[string]*ActionStatus)}
	if err := s.load(); err != nil {
		return nil, err
	}
	return s, nil
}

func (s *IdempotencyStore) load() error {
	bytes, err := os.ReadFile(s.path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("failed to read idempotency store: %w", err)
	}
	if len(bytes) == 0 {
		return nil
	}
	return json.Unmarshal(bytes, &s.data)
}

// persist must be called with s.mu held.
func (s *IdempotencyStore) persist() error {
	bytes, err := json.MarshalIndent(s.data, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.path, bytes, 0o644)
}

// Reserve creates a new pending record for actionID. Returns
// (existing status, true) if actionID was already reserved -- the caller
// must treat this as a replay and must NOT dispatch a second action.
func (s *IdempotencyStore) Reserve(actionID string) (*ActionStatus, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if existing, ok := s.data[actionID]; ok {
		return existing, true
	}

	now := time.Now().UTC()
	status := &ActionStatus{
		ActionID:  actionID,
		State:     StatePending,
		CreatedAt: now,
		UpdatedAt: now,
	}
	s.data[actionID] = status
	_ = s.persist()
	return status, false
}

// UpdateResult records the terminal (or settlement) state for actionID.
// Idempotent: calling it twice with the same terminal state is a no-op.
func (s *IdempotencyStore) UpdateResult(actionID, state, errorCode string, settled bool) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	status, ok := s.data[actionID]
	if !ok {
		// Result arrived for an actionID we never reserved (e.g. store was
		// reset) -- record it anyway so status polling still works.
		status = &ActionStatus{ActionID: actionID, CreatedAt: time.Now().UTC()}
		s.data[actionID] = status
	}
	status.State = state
	status.ErrorCode = errorCode
	status.Settled = settled
	status.UpdatedAt = time.Now().UTC()
	return s.persist()
}

// SetPaymentData attaches the verified payment payload/requirements to an
// already-reserved action record, so the execution watcher can settle it
// later without re-deriving anything from the original HTTP request.
func (s *IdempotencyStore) SetPaymentData(actionID string, payload, requirements json.RawMessage) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	status, ok := s.data[actionID]
	if !ok {
		return fmt.Errorf("no reserved action %q to attach payment data to", actionID)
	}
	status.PaymentPayload = payload
	status.PaymentRequirements = requirements
	status.UpdatedAt = time.Now().UTC()
	return s.persist()
}

func (s *IdempotencyStore) Get(actionID string) (*ActionStatus, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	status, ok := s.data[actionID]
	return status, ok
}
