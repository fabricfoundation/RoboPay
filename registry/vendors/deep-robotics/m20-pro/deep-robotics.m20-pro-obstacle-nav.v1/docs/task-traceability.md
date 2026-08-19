# Task Traceability

Maps each bounty requirement to the concrete evidence in this repo.

## Bounty requirements (deep-robotics-m20-pro-tier-1)

| Requirement | Evidence |
|---|---|
| Connect RoboPay with a robot in simulation, action triggered by the policy | `bridge/m20_pro_zenoh_bridge.py` dispatches to `simulation/runners/m20_pro_runner.py`, which runs a potential-field navigation policy in a closed loop -- not a scripted trajectory. |
| Simulator-only submission | MuJoCo + Webots R2025a. No physical hardware referenced anywhere in this profile. |
| Approved simulator (Isaac Sim, Gazebo, MuJoCo, Webots, etc.) | MuJoCo and Webots both used. |
| Cannot simply replay a predefined animation/trajectory | The navigation policy (`m20_pro_runner.py::_navigate_step`) computes a fresh (vx, wz) command every step from the live base pose and live obstacle positions read from the simulator; the trot gait (`_apply_leg_gait`) is computed live from simulation time each step. |
| Cannot rely solely on built-in demo motions; must be triggered by policy/planner/controller | Triggered by `_navigate_step()`, a potential-field local planner, not a MuJoCo/Webots built-in demo. |
| Must provide simulator state metrics | `docs/evidence/m20_pro_metrics.json` and `simulation/webots/results/webots_metrics.json` report `displacement_m`, `path_length_m`, `collisions`, `target_distance_remaining_m`, `status`, `sim_steps`/`sim_seconds`, computed from live simulator state, not hardcoded. |
| Must include Sim-to-Sim validation | `simulation/validation/validate_sim_to_sim.py`, verified PASSED: `displacement_m` differs by 0.0011m, `target_distance_remaining_m` by 0.0011m, both report `status=goal_reached` and `collisions=0`. |
| Example category: obstacle navigation | `m20_pro_obstacle_navigation` skill: navigate to a goal pose while avoiding three static obstacles. |

## RoboPay payment-integration gate

The bar for this gate was set by the review history of related tier-1
submissions in this repo (Boston Dynamics Spot): a real, fail-closed
x402 payment flow through the shared Go tunnel, not a bridge-level
payment check. This submission was rebuilt against that same
architecture rather than inventing a robot-local alternative.

| Concern | How it's addressed here |
|---|---|
| Action gate fails open (accepts a request with no registered action/skill) | `tunnel/internal/handlers/handlers.go::PostAction` requires a JSON body with a non-empty `action` field present on the `ALLOWED_ACTIONS` allowlist; an empty allowlist and an unlisted action are both rejected (503/403) before `PublishRobotAction` is ever called. Covered by `TestPostAction_RejectsPayloadWithoutAction`, `TestPostAction_RejectsWhenAllowlistEmpty`, `TestPostAction_RejectsUnknownAction`. |
| No immediate accepted/pending response + terminal result carrying the same actionId | `PostAction` returns `202 {status, state, actionId, status_url}` immediately; `GET /action/:id/status` (`handlers.go::GetActionStatus`) serves the durable terminal state by the same `actionId`. Covered by `TestGetActionStatus_ReturnsReservedPendingState`. |
| Replay must not cause a second action / second settlement | `idempotency.go::IdempotencyStore.Reserve` is called and reserves the `actionId` slot *before* publish; a second `Reserve` with the same `actionId` is flagged as a replay. Persisted to disk on every transition, so it survives a tunnel restart (`TestIdempotencyStore_PersistsAcrossReopen`). Bridge-side, `replay_guard.py` adds a second, independent dedup layer keyed by the same `actionId` (`test_replayed_action_id_rejected_without_second_dispatch`). |
| Settlement occurs at accept time / on any 2xx response, not gated on real success | The stock auto-settling `ginmw.X402Payment` middleware was replaced with `payment_gate.go::X402VerifyOnly`, which verifies but never settles. `settlement_watcher.go::ExecutionWatcher` is the *only* code path that calls `ProcessSettlement`, and only in response to a terminal `robot/tunnel/result` with `status=success` for the matching `actionId`. Proven end-to-end in `tunnel/cmd/e2e_test.go::TestE2E_SettlementOnlyAfterTerminalSuccess`. |
| Invalid/unverified payment must never reach the simulator | `X402VerifyOnly` runs `ProcessHTTPRequest` (real x402 verify against the facilitator) before `c.Next()` reaches `PostAction`; a rejected/unverified payment returns 402 and never calls `PublishRobotAction`. Covered by `TestX402VerifyOnly_TamperedSignature_RejectsWithoutCallingHandler`. |
| Bridge-level payment validation drift from what the tunnel actually sends | Removed entirely: the old bridge validated `payment.verified`/`payment.status` fields on the Zenoh payload -- provably dead code once the tunnel stopped publishing payment fields to `robot/tunnel/action`. The bridge now trusts the tunnel's gate. |

## Live payment proof

A live Base Sepolia transaction backs this submission:
`docs/evidence/base-sepolia/live-payment-e2e.md` -- real wallet-signed
EIP-3009 payment, real x402.org facilitator, real on-chain USDC
settlement (tx
`0x36000cc766fc95f7f1cfe8f2500a31cc98d236e98d050738553de555f1439587`,
status SUCCESS, block 45531604, independently verifiable via any Base
Sepolia RPC).

## Known limitations (stated explicitly, not hidden)

- The M20 Pro base is mounted on a planar (x, y, yaw) joint rather than
  a full 6-DOF free body balanced purely through leg-ground contact --
  a deliberate simplification for a Tier 1 navigation skill, disclosed
  in `docs/validation-report.md`.
- The production Fabric WebSocket proxy transport itself
  (`tunnel/internal/client.go`) is not exercised in the live payment
  test, since this environment cannot reach the Fabric proxy --
  `tunnel/cmd/localserver` substitutes an identical router bound to a
  real local TCP port instead. The recording-facilitator suite
  (`tunnel/cmd/e2e_test.go`) remains as fast, deterministic CI
  coverage of the same verify/settle separation.
- The bridge-local `replay_guard.py` SQLite store is a secondary,
  belt-and-braces dedup layer scoped to one bridge process; the
  tunnel's file-backed `IdempotencyStore` is the authoritative,
  restart-surviving replay guard for payment/settlement purposes.
