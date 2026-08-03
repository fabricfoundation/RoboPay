// Package settle runs the deferred settlement sweep: it watches the
// ledger for actions that finished successfully but have not yet been
// paid out, and calls the x402 facilitator's Settle for each one.
//
// Design choice: a polling loop over UnsettledSuccessActionIDs, not a
// channel/event pushed from the handler that marks success. A polling
// sweep is trivially resumable after a crash (nothing in-flight is
// lost, it just gets picked up on the next tick) and needs no extra
// coordination with the handler goroutine -- the ledger is already the
// single source of truth both sides read from.
package settle

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"go.uber.org/zap"

	"github.com/fabricfoundation/tunnel/internal/ledger"
)

// FacilitatorSettler is the subset of HTTPFacilitatorClient this
// package depends on. Defined here (not imported from x402http)
// so tests can substitute a fake without a live facilitator.
type FacilitatorSettler interface {
	Settle(ctx context.Context, payloadBytes, requirementsBytes []byte) (*SettleResponse, error)
}

// SettleResponse mirrors the fields of x402.SettleResponse that this
// package actually consumes. Declared locally to keep this package's
// dependency surface on the x402 SDK minimal and explicit.
type SettleResponse struct {
	Success      bool
	ErrorReason  string
	ErrorMessage string
	Transaction  string
	Network      string
}

// Watcher periodically sweeps the ledger for successful-but-unsettled
// actions and settles them.
type Watcher struct {
	Ledger      *ledger.Ledger
	Facilitator FacilitatorSettler
	Logger      *zap.Logger
	Interval    time.Duration
}

// New constructs a Watcher. interval controls how often the ledger is
// swept; a small interval (e.g. 2s) keeps settlement latency low
// without hammering the facilitator, since each tick only does work
// when there's something unsettled.
func New(ldg *ledger.Ledger, facilitator FacilitatorSettler, logger *zap.Logger, interval time.Duration) *Watcher {
	if interval <= 0 {
		interval = 2 * time.Second
	}
	return &Watcher{
		Ledger:      ldg,
		Facilitator: facilitator,
		Logger:      logger,
		Interval:    interval,
	}
}

// Run blocks, sweeping on Interval until ctx is cancelled. Intended to
// be started in its own goroutine from main.
func (w *Watcher) Run(ctx context.Context) {
	ticker := time.NewTicker(w.Interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			w.sweepOnce(ctx)
		}
	}
}

// sweepOnce settles every currently-unsettled successful action. A
// failure settling one action is logged and left for the next tick --
// it must never block or skip the others in the same sweep.
func (w *Watcher) sweepOnce(ctx context.Context) {
	ids := w.Ledger.UnsettledSuccessActionIDs()
	for _, actionID := range ids {
		if err := w.settleOne(ctx, actionID); err != nil {
			w.Logger.Warn("settlement attempt failed, will retry next sweep",
				zap.String("action_id", actionID), zap.Error(err))
		}
	}
}

// settleOne settles a single action. Returns an error (not a panic or
// silent skip) so the caller can log and retry on the next sweep --
// this action's ledger entry is left exactly as it was on failure, so
// it naturally reappears in UnsettledSuccessActionIDs next tick.
func (w *Watcher) settleOne(ctx context.Context, actionID string) error {
	payload, requirements, err := w.Ledger.Payment(actionID)
	if err != nil {
		return fmt.Errorf("load payment context: %w", err)
	}
	if payload == nil || requirements == nil {
		// No payment was ever attached (e.g. a free/no-payment-required
		// skill). Nothing to settle -- mark settled with no tx so this
		// action stops showing up in the unsettled sweep.
		return w.Ledger.MarkSettled(actionID, "", "")
	}

	payloadBytes, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal payment payload: %w", err)
	}
	requirementsBytes, err := json.Marshal(requirements)
	if err != nil {
		return fmt.Errorf("marshal payment requirements: %w", err)
	}

	resp, err := w.Facilitator.Settle(ctx, payloadBytes, requirementsBytes)
	if err != nil {
		return fmt.Errorf("facilitator settle: %w", err)
	}
	if resp == nil || !resp.Success {
		reason, msg := "", ""
		if resp != nil {
			reason, msg = resp.ErrorReason, resp.ErrorMessage
		}
		return fmt.Errorf("facilitator reported settlement failure: reason=%q message=%q", reason, msg)
	}

	return w.Ledger.MarkSettled(actionID, resp.Transaction, resp.Network)
}
