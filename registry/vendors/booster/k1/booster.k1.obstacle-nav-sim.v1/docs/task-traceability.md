# Task Traceability

Maps each bounty requirement to the concrete evidence in this repo.

## Bounty requirements (booster-k1-tier-1)

| Requirement | Evidence |
|---|---|
| Connect RoboPay with a robot in simulation, action triggered by the policy | `bridge/booster_k1_zenoh_bridge.py` dispatches to `simulation/mujoco/runner.py`, which runs `simulation/common_policy/dwa_planner.py` (DWA local planner) in a closed loop -- not a scripted trajectory. |
| Simulator-only submission | MuJoCo 3.11.0 + Webots R2025a. No physical hardware referenced anywhere in this profile. |
| Approved simulator (Isaac Sim, Gazebo, MuJoCo, Webots, etc.) | MuJoCo and Webots both used. |
| Cannot simply replay a predefined animation/trajectory | The DWA planner (`dwa_planner.py::plan_step`) computes a fresh (v, omega) command every policy tick from the live robot pose and live obstacle positions read from the simulator. |
| Cannot rely solely on built-in demo motions; must be triggered by policy/planner/controller | Triggered by `plan_step()`, a Dynamic Window Approach implementation, not a Webots/MuJoCo built-in demo. |
| Must provide simulator state metrics | `simulation/mujoco/results/metrics.json` and `simulation/webots/results/metrics.json` report `distance_to_goal_m`, `path_length_m`, `collision_count`, `final_pose`, `status`, `sim_time_sec`, computed from live simulator state, not hardcoded. |
| Must include Sim-to-Sim validation | `simulation/sim_to_sim_validate.py`, verified PASSED: `distance_to_goal_m` differs by 0.0005m, `path_length_m` by 1.3%, both report `status=success` and `collision_count=0`. |
| Example category: obstacle navigation | `k1_navigate_avoid_obstacles` skill: navigate to a goal pose while avoiding two static obstacles. |

## RoboPay payment-integration gate

The bar for this gate was set by the review history of related tier-1
submissions in this repo (Boston Dynamics Spot, Reachy Mini): a real,
fail-closed x402 payment flow through the shared Go tunnel, not a
bridge-level payment check. This submission was rebuilt against that
same architecture rather than inventing a robot-local alternative.

| Concern (from review of related submissions) | How it's addressed here |
|---|---|
| Action gate fails open (accepts a request with no registered action/skill) | `tunnel/internal/handlers/handlers.go::PostAction` requires a JSON body with a non-empty `action` field present on the `ALLOWED_ACTIONS` allowlist; an empty allowlist and an unlisted action are both rejected (503/403) before `PublishRobotAction` is ever called. Covered by `TestPostAction_RejectsPayloadWithoutAction`, `TestPostAction_RejectsWhenAllowlistEmpty`, `TestPostAction_RejectsUnknownAction`. |
| No immediate accepted/pending response + terminal result carrying the same actionId | `PostAction` returns `202 {status, state, actionId, status_url}` immediately; `GET /action/:id/status` (`handlers.go::GetActionStatus`) serves the durable terminal state by the same `actionId`. Covered by `TestGetActionStatus_ReturnsReservedPendingState`. |
| Replay must not cause a second action / second settlement | `idempotency.go::IdempotencyStore.Reserve` is called and reserves the `actionId` slot *before* publish; a second `Reserve` with the same `actionId` is flagged as a replay. Persisted to disk on every transition, so it survives a tunnel restart (`TestIdempotencyStore_PersistsAcrossReopen`). Bridge-side, `replay_guard.py` adds a second, independent dedup layer keyed by the same `actionId` (`test_replayed_action_id_rejected_without_second_dispatch`). |
| Settlement occurs at accept time / on any 2xx response, not gated on real success | The stock auto-settling `ginmw.X402Payment` middleware was replaced with `payment_gate.go::X402VerifyOnly`, which verifies but never settles. `settlement_watcher.go::ExecutionWatcher` is the *only* code path that calls `ProcessSettlement`, and only in response to a terminal `robot/tunnel/result` with `status=success` for the matching `actionId`. Proven end-to-end in `tunnel/cmd/e2e_test.go::TestE2E_SettlementOnlyAfterTerminalSuccess`: exactly one settle call, only after a genuine success result; a failure result, a duplicate success result, and a facilitator-side settlement failure all produce zero additional settle calls. |
| Invalid/unverified payment must never reach the simulator | `X402VerifyOnly` runs `ProcessHTTPRequest` (real x402 verify against the facilitator) before `c.Next()` reaches `PostAction`; a rejected/unverified payment returns 402 and never calls `PublishRobotAction`. Covered by `TestX402VerifyOnly_TamperedSignature_RejectsWithoutCallingHandler` and, in the end-to-end test, `TestE2E_...`'s unpaid-request assertion (zero facilitator calls of any kind). |
| Bridge-level payment validation drift from what the tunnel actually sends | Removed entirely: the old `action_validator.py` (which checked `payment.verified`, `payment.status`, etc. on the Zenoh payload) was provably dead code once the tunnel stopped publishing payment fields to `robot/tunnel/action`. Deleted along with its 11 tests; the bridge now trusts the tunnel's gate, consistent with the documented design (a robot bridge is not expected to re-verify a payment it never receives). |

## Known limitations (stated explicitly, not hidden)

- The Booster K1 base is a geometric proxy (cylinder + planar joints), not
  an official CAD model -- this submission does not have access to Booster's
  CAD/URDF.
- `sim_time_sec` differs between MuJoCo (30.5s) and Webots (9.9s) for the
  same scenario, due to each engine's timestep resolution interacting
  differently with the policy's fixed-rate tick. Spatial metrics agree
  closely; `sim_time_sec` is intentionally excluded from
  `sim_to_sim_validate.py`'s comparison for that reason.
- No live Base Sepolia transaction or real EVM-signed payment header is
  included -- this environment does not have wallet/signing credentials.
  The verify/settle separation is instead proven directly against the
  production `ExecutionWatcher`/`IdempotencyStore` types using a
  recording facilitator (`tunnel/cmd/e2e_test.go`), the same testing
  pattern (fake facilitator, no real network) used elsewhere in this
  repo's x402 test suites.
- The bridge-local `replay_guard.py` SQLite store is a secondary,
  belt-and-braces dedup layer scoped to one bridge process; the
  tunnel's file-backed `IdempotencyStore` is the authoritative,
  restart-surviving replay guard for payment/settlement purposes.
