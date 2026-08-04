package handlers

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// replayRetention is how long terminal replay records stay on disk. It is
// intentionally much longer than the old in-memory 10-minute TTL so that a
// tunnel restart cannot be used to re-run an already-actuated payment.
const replayRetention = 24 * time.Hour

var (
	// ErrReplayDetected is returned when an idempotency key was already used.
	ErrReplayDetected = errors.New("duplicate idempotency key")
	// ErrPaymentReplayed is returned when the exact same x402 payment payload
	// was already bound to a previous action, regardless of idempotency key.
	ErrPaymentReplayed = errors.New("payment payload already used for a previous action")
)

type replayRecord struct {
	Key         string          `json:"key"`
	PaymentHash string          `json:"payment_hash,omitempty"`
	ActionID    string          `json:"action_id,omitempty"`
	RobotID     string          `json:"robot_id,omitempty"`
	SkillID     string          `json:"skill_id,omitempty"`
	ParamsHash  string          `json:"params_hash,omitempty"`
	Status      string          `json:"status"`
	ErrorCode   string          `json:"error_code,omitempty"`
	Result      json.RawMessage `json:"result,omitempty"`
	// Settlement is recorded only after a successful deferred x402 settlement
	// so GET /action/:id/status can serve the receipt across restarts.
	Settlement *SettlementRecord `json:"settlement,omitempty"`
	UpdatedAt  time.Time         `json:"updated_at"`
}

// SettlementRecord is the durable x402 settlement receipt for an action.
type SettlementRecord struct {
	Transaction     string `json:"transaction,omitempty"`
	Network         string `json:"network,omitempty"`
	Payer           string `json:"payer,omitempty"`
	PaymentResponse string `json:"payment_response,omitempty"`
}

// ActionStatus is the queryable view of a record for the status endpoint.
type ActionStatus struct {
	Key        string
	ActionID   string
	RobotID    string
	SkillID    string
	ParamsHash string
	Status     string
	ErrorCode  string
	Result     json.RawMessage
	Settlement *SettlementRecord
	UpdatedAt  time.Time
}

// ReplayStore is a durable, payment-bound idempotency store. Every record is
// persisted to disk before the action is allowed to proceed, so a process
// restart (or crash between publish and response) cannot re-actuate the
// simulator for the same idempotency key or the same x402 payment payload.
type ReplayStore struct {
	mu      sync.Mutex
	path    string
	records map[string]replayRecord
	// loadErr is sticky: accepting an action after an unreadable or corrupt
	// durable store would turn a restart into a replay bypass.  Reserve and
	// all state mutations reject while it is set, so payment safety fails
	// closed until an operator restores the store deliberately.
	loadErr error
}

// NewReplayStore loads (or lazily creates) the store backing file at path.
func NewReplayStore(path string) *ReplayStore {
	store := &ReplayStore{path: path, records: make(map[string]replayRecord)}
	raw, err := os.ReadFile(path)
	switch {
	case err == nil:
		var loaded map[string]replayRecord
		if err := json.Unmarshal(raw, &loaded); err != nil || loaded == nil {
			if err == nil {
				err = errors.New("idempotency store must contain a JSON object")
			}
			store.loadErr = fmt.Errorf("load idempotency store: %w", err)
			return store
		}
		store.records = loaded
	case errors.Is(err, os.ErrNotExist):
		// A first deployment has no state yet.  It becomes durable before the
		// first publication in Reserve.
	default:
		store.loadErr = fmt.Errorf("read idempotency store: %w", err)
		return store
	}
	store.pruneLocked(time.Now())
	return store
}

// NewReplayStoreFromEnv builds the store from IDEMPOTENCY_STORE_PATH, falling
// back to a file in the working directory so durability is on by default.
func NewReplayStoreFromEnv() *ReplayStore {
	path := os.Getenv("IDEMPOTENCY_STORE_PATH")
	if path == "" {
		path = "robopay_idempotency.json"
	}
	return NewReplayStore(path)
}

func (s *ReplayStore) pruneLocked(now time.Time) {
	for key, record := range s.records {
		if now.Sub(record.UpdatedAt) > replayRetention {
			delete(s.records, key)
		}
	}
}

// Reserve durably claims key (and, when present, the payment payload hash)
// before anything is published to the robot. The write is persisted before
// returning nil; a persistence failure rejects the action (fail closed).
func (s *ReplayStore) Reserve(key, paymentHash, actionID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.loadErr != nil {
		return s.loadErr
	}
	now := time.Now()
	s.pruneLocked(now)

	if key != "" {
		if _, exists := s.records[key]; exists {
			return ErrReplayDetected
		}
	}
	if paymentHash != "" {
		for _, record := range s.records {
			if record.PaymentHash == paymentHash {
				return ErrPaymentReplayed
			}
		}
	}
	if key == "" && paymentHash == "" {
		return nil
	}
	storageKey := key
	if storageKey == "" {
		storageKey = "payment:" + paymentHash
	}
	s.records[storageKey] = replayRecord{
		Key:         storageKey,
		PaymentHash: paymentHash,
		ActionID:    actionID,
		Status:      "reserved",
		UpdatedAt:   now,
	}
	if err := s.persistLocked(); err != nil {
		delete(s.records, storageKey)
		return err
	}
	return nil
}

// MarkOutcome records the terminal state of a reserved key. Records are kept
// (not deleted) on failure/timeout so a replay after failure still gets 409.
func (s *ReplayStore) MarkOutcome(key, status string) error {
	return s.MarkOutcomeDetails(key, status, "", nil)
}

// MarkOutcomeDetails records the terminal state together with the error code
// and, on settled success, the x402 settlement receipt. Like MarkOutcome the
// record is persisted and never deleted before the retention window ends.
func (s *ReplayStore) MarkOutcomeDetails(key, status, errorCode string, settlement *SettlementRecord) error {
	return s.MarkOutcomeWithResult(key, status, errorCode, settlement, nil)
}

// MarkOutcomeWithResult persists the bridge's structured terminal result next
// to the settlement state so GET /action/:id/status remains useful after a
// restart and cannot be confused with an unrelated action.
func (s *ReplayStore) MarkOutcomeWithResult(key, status, errorCode string, settlement *SettlementRecord, result json.RawMessage) error {
	if key == "" {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.loadErr != nil {
		return s.loadErr
	}
	record, exists := s.records[key]
	if !exists {
		return nil
	}
	record.Status = status
	record.ErrorCode = errorCode
	if settlement != nil {
		record.Settlement = settlement
	}
	if result != nil {
		record.Result = append(json.RawMessage(nil), result...)
	}
	record.UpdatedAt = time.Now()
	s.records[key] = record
	return s.persistLocked()
}

// BindActionMetadata makes the durable record carry the complete correlation
// tuple before publication. A persistence failure keeps the action fail-closed.
func (s *ReplayStore) BindActionMetadata(key string, metadata actionMetadata) error {
	if key == "" {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.loadErr != nil {
		return s.loadErr
	}
	record, exists := s.records[key]
	if !exists {
		return errors.New("idempotency reservation not found")
	}
	record.ActionID = metadata.ActionID
	record.RobotID = metadata.RobotID
	record.SkillID = metadata.SkillID
	record.ParamsHash = metadata.ParamsHash
	record.UpdatedAt = time.Now()
	s.records[key] = record
	return s.persistLocked()
}

// StatusByActionID returns the durable execution/settlement state for the
// status endpoint. The lookup scans records because the store is keyed by
// idempotency key; sizes are small (24h retention).
func (s *ReplayStore) StatusByActionID(actionID string) (ActionStatus, bool) {
	if actionID == "" {
		return ActionStatus{}, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.loadErr != nil {
		return ActionStatus{}, false
	}
	for _, record := range s.records {
		if record.ActionID == actionID {
			return ActionStatus{
				Key:        record.Key,
				ActionID:   record.ActionID,
				RobotID:    record.RobotID,
				SkillID:    record.SkillID,
				ParamsHash: record.ParamsHash,
				Status:     record.Status,
				ErrorCode:  record.ErrorCode,
				Result:     append(json.RawMessage(nil), record.Result...),
				Settlement: record.Settlement,
				UpdatedAt:  record.UpdatedAt,
			}, true
		}
	}
	return ActionStatus{}, false
}

// Release drops a reservation. Only valid before anything was published to
// the robot (e.g. marshal or publish failure); once an action may have
// actuated, the record must be kept via MarkOutcome instead.
func (s *ReplayStore) Release(key string) {
	if key == "" {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.records, key)
	_ = s.persistLocked()
}

func (s *ReplayStore) persistLocked() error {
	raw, err := json.Marshal(s.records)
	if err != nil {
		return err
	}
	if dir := filepath.Dir(s.path); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	temp := s.path + ".tmp"
	if err := os.WriteFile(temp, raw, 0o600); err != nil {
		return err
	}
	return os.Rename(temp, s.path)
}
