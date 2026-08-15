package handlers

import (
	"context"
	"encoding/json"
	"time"

	x402 "github.com/x402-foundation/x402/go"
	x402http "github.com/x402-foundation/x402/go/http"
	"github.com/x402-foundation/x402/go/types"
	"go.uber.org/zap"
)

// SettlementProcessor is satisfied by *x402http.HTTPServer. Declared as an
// interface so ExecutionWatcher is unit-testable against a fake facilitator.
type SettlementProcessor interface {
	ProcessSettlement(
		ctx context.Context,
		payload types.PaymentPayload,
		requirements types.PaymentRequirements,
		overrides *x402.SettlementOverrides,
		transportContext *x402http.HTTPTransportContext,
		declaredExtensions map[string]interface{},
	) *x402http.ProcessSettleResult
}

// robotResult mirrors the fields the Python bridge publishes on
// robot/tunnel/result (bridge/booster_k1_zenoh_bridge.py::make_result).
type robotResult struct {
	ActionID string `json:"actionId"`
	Status   string `json:"status"` // "success" | "error" | "rejected"
}

// ExecutionWatcher consumes terminal robot/tunnel/result events and is the
// ONLY place in this tunnel that may call ProcessSettlement. It never
// settles at HTTP-accept time, and it settles an actionId at most once.
type ExecutionWatcher struct {
	Store   *IdempotencyStore
	Settler SettlementProcessor
	Logger  *zap.Logger
	Timeout time.Duration
}

func NewExecutionWatcher(store *IdempotencyStore, settler SettlementProcessor, logger *zap.Logger) *ExecutionWatcher {
	return &ExecutionWatcher{Store: store, Settler: settler, Logger: logger, Timeout: 30 * time.Second}
}

// HandleResult processes one robot/tunnel/result message.
func (w *ExecutionWatcher) HandleResult(payload []byte) {
	var res robotResult
	if err := json.Unmarshal(payload, &res); err != nil {
		w.Logger.Warn("execution watcher: invalid result JSON", zap.Error(err))
		return
	}
	if res.ActionID == "" {
		w.Logger.Warn("execution watcher: result missing actionId, cannot correlate")
		return
	}

	status, ok := w.Store.Get(res.ActionID)
	if !ok {
		w.Logger.Warn("execution watcher: result for unknown actionId", zap.String("action_id", res.ActionID))
		return
	}
	if status.Settled {
		// Duplicate/replayed result for an action already settled -- never
		// settle twice.
		return
	}

	if res.Status != "success" {
		_ = w.Store.UpdateResult(res.ActionID, StateFailed, "SIMULATOR_"+res.Status, false)
		w.Logger.Info("execution watcher: non-success result, not settling",
			zap.String("action_id", res.ActionID), zap.String("status", res.Status))
		return
	}

	if len(status.PaymentPayload) == 0 || len(status.PaymentRequirements) == 0 {
		w.Logger.Error("execution watcher: success result but no stored payment data, cannot settle",
			zap.String("action_id", res.ActionID))
		_ = w.Store.UpdateResult(res.ActionID, StateSettlementFailed, "MISSING_PAYMENT_DATA", false)
		return
	}

	var paymentPayload types.PaymentPayload
	var paymentRequirements types.PaymentRequirements
	if err := json.Unmarshal(status.PaymentPayload, &paymentPayload); err != nil {
		w.Logger.Error("execution watcher: failed to decode stored payment payload", zap.Error(err))
		_ = w.Store.UpdateResult(res.ActionID, StateSettlementFailed, "PAYLOAD_DECODE_ERROR", false)
		return
	}
	if err := json.Unmarshal(status.PaymentRequirements, &paymentRequirements); err != nil {
		w.Logger.Error("execution watcher: failed to decode stored payment requirements", zap.Error(err))
		_ = w.Store.UpdateResult(res.ActionID, StateSettlementFailed, "REQUIREMENTS_DECODE_ERROR", false)
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), w.Timeout)
	defer cancel()

	settleResult := w.Settler.ProcessSettlement(ctx, paymentPayload, paymentRequirements, nil, nil, nil)

	if settleResult == nil || !settleResult.Success {
		errReason := "UNKNOWN"
		if settleResult != nil {
			errReason = settleResult.ErrorReason
		}
		w.Logger.Error("execution watcher: settlement failed after successful execution",
			zap.String("action_id", res.ActionID), zap.String("reason", errReason))
		_ = w.Store.UpdateResult(res.ActionID, StateSettlementFailed, "SETTLE_FAILED: "+errReason, false)
		return
	}

	_ = w.Store.UpdateResult(res.ActionID, StateSucceeded, "", true)
	w.Logger.Info("execution watcher: settled",
		zap.String("action_id", res.ActionID), zap.String("transaction", settleResult.Transaction))
}
