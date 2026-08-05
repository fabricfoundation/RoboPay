package settlement

import (
	"sync"
	"time"
)

// SettlementManager tracks payment settlement status per actionId.
// Settlement only occurs on successful terminal results.
type SettlementManager struct {
	mu       sync.RWMutex
	settled  map[string]bool       // actionId -> settled (success only)
	pending  map[string]time.Time  // actionId -> submission time (for cleanup)
	results  map[string]ResultEnvelope // actionId -> terminal result
}

// ResultEnvelope represents the terminal result from the robot.
type ResultEnvelope struct {
	ActionID string                 `json:"actionId"`
	RobotID  string                 `json:"robotId"`
	SkillID  string                 `json:"skillId"`
	Status   string                 `json:"status"` // "success", "error", etc.
	Metrics  map[string]interface{} `json:"metrics,omitempty"`
	Code     string                 `json:"code,omitempty"`
	Message  string                 `json:"message,omitempty"`
}

// NewSettlementManager creates a new settlement manager.
func NewSettlementManager() *SettlementManager {
	return &SettlementManager{
		settled: make(map[string]bool),
		pending: make(map[string]time.Time),
		results: make(map[string]ResultEnvelope),
	}
}

// MarkPending records that an action has been submitted (accepted/pending).
func (s *SettlementManager) MarkPending(actionID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pending[actionID] = time.Now()
}

// ProcessResult consumes a terminal result and gates settlement.
// Only "success" status results in settlement.
func (s *SettlementManager) ProcessResult(result ResultEnvelope) {
	s.mu.Lock()
	defer s.mu.Unlock()

	s.results[result.ActionID] = result

	// Settlement gate: ONLY settle on success
	if result.Status == "success" {
		s.settled[result.ActionID] = true
	}
	// Explicitly do NOT settle on failure, timeout, error, etc.
}

// IsSettled returns true if the actionId has been settled (success only).
func (s *SettlementManager) IsSettled(actionID string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.settled[actionID]
}

// GetResult returns the terminal result for an actionId, if available.
func (s *SettlementManager) GetResult(actionID string) (ResultEnvelope, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	result, ok := s.results[actionID]
	return result, ok
}

// CleanupOldEntries removes entries older than maxAge to prevent memory leaks.
func (s *SettlementManager) CleanupOldEntries(maxAge time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()

	cutoff := time.Now().Add(-maxAge)
	for actionID, submitTime := range s.pending {
		if submitTime.Before(cutoff) {
			delete(s.pending, actionID)
			delete(s.settled, actionID)
			delete(s.results, actionID)
		}
	}
}