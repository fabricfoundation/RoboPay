# Validation Report — pollen-robotics.reachy-mini.mujoco-webots-sim.v1

Robot instance: `reachy-mini-kauker` · Track: Hugging Face Reachy Mini (Tier 1)
Scope: simulator-only (MuJoCo primary + Webots cross-check) · Payment: x402 on Base Sepolia (REAL settlement)

This report records what was actually exercised, with the concrete test that
proves each row. Nothing here is `simulated: true` — the paid path settles a
real USDC transfer on Base Sepolia, verifiable on BaseScan.

## Payment / settlement contract

| # | Scenario | Expected | Evidence |
|---|----------|----------|----------|
| 1 | Unpaid request | `402 Payment Required` + `PAYMENT-REQUIRED` header | `bridge/reachy_mini/test_payment_gate.py` |
| 2 | Paid request, real chain | `202 accepted/pending` with actionId, then status `succeeded` + settlement receipt on `GET /action/<id>/status` | `bridge/reachy_mini/test_base_sepolia_tunnel_e2e.py` (real tx hashes below) |
| 3 | Settlement only after success | `/settle` called exactly once, strictly after the correlated success result | `handlers_test.go::TestPostAction_SettlesOnlyAfterSimulatorSuccess` + `test_e2e_paid_action.py` |
| 4 | Duplicate idempotency key | `409 REPLAY_DETECTED` — durable file-backed store, 24h retention, no re-settle | `handlers_test.go::TestPostAction_RejectsReplay` |
| 5 | Replay after tunnel restart | `409 REPLAY_DETECTED`, exactly one actuation, status survives restart | `handlers_test.go::TestPostAction_ReplayRejectedAfterRestart` + `test_x402_no_settlement.py` (real process restart) |
| 6 | Same payment, fresh action/idempotency key | `409 PAYMENT_REPLAY_DETECTED` (payment-bound idempotency) | `handlers_test.go::TestPostAction_RejectsPaymentReplayWithFreshKey` |
| 7 | Simulator execution failure | status `failed` / `SIMULATOR_EXECUTION_FAILED`, **zero /settle calls** | `handlers_test.go::TestPostAction_DoesNotSettleOnSimulatorFailure` + `test_x402_no_settlement.py` (recording facilitator) |
| 8 | Simulator result timeout | status `timeout` / `SIMULATOR_RESULT_TIMEOUT`, **zero /settle calls** | `handlers_test.go::TestPostAction_TimesOutWithoutSettlementAndKeepsReservation` + `test_x402_no_settlement.py` |
| 9 | Missing/empty `action` | `400 MISSING_ACTION` (fail closed, no default) | `handlers_test.go::TestPostAction_RejectsPayloadWithoutAction` |
| 10 | Allowlist not configured | `503 ALLOWLIST_NOT_CONFIGURED` (fail closed) | `handlers_test.go::TestPostAction_FailsClosedWithoutAllowlist` |
| 11 | Unknown/disallowed skill | `403 SKILL_NOT_ALLOWED` before Zenoh publication | `handlers_test.go::TestPostAction_RejectsUnknownSkill` / `RejectsSkillOutsideAllowlist` |
| 12 | Wrong robot id | `403 WRONG_ROBOT` | `handlers_test.go::TestPostAction_WrongRobot` |
| 13 | Invalid params / over-duration | `400 INVALID_PARAMS` / `400 DURATION_LIMIT` | `handlers_test.go` |
| 14 | Settlement failure after success | status `settlement_failed`, `settled=false` (never silently charged) | `handlers_test.go::TestPostAction_SettlementFailureIsSurfaced` |

**Settlement invariant:** `POST /action` verifies the x402 payment and answers
`202 accepted/pending` immediately; the facilitator's `/settle` runs only in
the execution watcher, strictly after the simulator publishes a correlated
`robot/tunnel/result` with status `success`. The terminal state and receipt
are durable and served by `GET /action/<id>/status` under the same actionId.
This is not only a unit-level claim: `bridge/reachy_mini/test_x402_no_settlement.py`
runs the **real tunnel binary with the real x402 verify path** against a
recording facilitator, injects simulator failure and timeout, replays payments
and restarts the tunnel process — and asserts the facilitator received **zero
`/settle` calls** across all scenarios.

`ALLOWED_ACTIONS` is a required deployment setting. The Tunnel does not derive
an allowlist from the robot profile at runtime: an absent or empty value leaves
the handler with no permitted skill and returns `503 ALLOWLIST_NOT_CONFIGURED`.

### Real Base Sepolia settlements (paid path)

Example transactions produced by the paid E2E run (BaseScan
`https://sepolia.basescan.org/tx/<hash>`):

- `0x49f9b6e4111774a85a20adfe0aaa9633be33872ea7062b0127b64483ceb13d74`
- `0x5bdba3a0e3af61b76bab1c9da973f8902694e42e93cf1124ddf9424460091421`
- `0xd322cd3d06a03b57aa4da8c3277a70d3daa52f1397324a31c00332a34e7799e6`
- `0xc566459096236f33d541c9d6db340be452132677f11c22fcdfe565943a5bf9b9`
- `0xcfa46b97d259d47c36588332bd3462094d5b3f4962e27877dfde12513ed05c97`

Asset: USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (6 decimals),
price `0.001` USDC per action, network `eip155:84532`.

## Motion / task quality

| Metric | Source | Result |
|--------|--------|--------|
| `head_pose_source` | Webots supervisor `getPosition`/`getOrientation` | `supervisor_node` (ground-truth, not estimated) |
| `target_pose_error_rad` | measured vs. object direction | converges within tracking phase |
| `sim2sim_robustness_score` | same policy, MuJoCo vs. Webots | `1.0` across apple / croissant / duck |
| `success_rate` | per-episode | tracked per run in metrics topic |

Policy: `ReachyTaskPolicy` FSM (`SCANNING -> TRACKING -> EXPRESSIVE`), 9 DOF,
per-step P-control + slew-rate limiting, recomputed every 5 ms from live state.
No recorded trajectory or canned animation is replayed; the EXPRESSIVE phase is
state-conditioned, not a fixed clip.

## CI evidence

`.github/workflows/reachy-tier1.yml` runs `make test` (`go test -v ./...`, including
`handlers_test.go`) and the **mandatory** real-Tunnel
`test_x402_no_settlement.py` on every push/PR. That test observes the unpaid
`402`, injected failure/timeout, restart replay rejection, and zero `/settle`
calls without `continue-on-error`. The `sim2sim-webots` job runs the same
policy in MuJoCo and Webots and uploads its result. `base-sepolia-e2e` is a
manual `workflow_dispatch` job only, so a normal push never spends testnet
funds; when explicitly run with repository secrets, it generates and uploads
the public `base_sepolia_result*.json` artifact written by
`test_base_sepolia_tunnel_e2e.py`.

## Robot identity binding

The shared Tunnel/Gateway WebSocket protocol identifies the robot by `?id=`
and does not yet define a signed identity handshake. Per maintainer guidance
this contribution does not invent one: the signed robot↔payee binding is
tracked as an **upstream protocol dependency**. Today the binding is
configuration-level (the Tunnel's deployment config or `ROBOT_ID` and
`ROBO_PAYEE_ADDRESS` overrides, enforced by `403 WRONG_ROBOT`) — see the root
README's fail-closed paid-action contract. The checked-in `tunnel/config.json`
remains a generic local example rather than embedding this robot/payee.

## Known limitations (honest scope)

- **No arms.** Reachy Mini is a head + antennae platform; the Tier 1 task is
  expressive closed-loop head-tracking, not manipulation. This is a hardware
  property of the robot, not a shortcut.
- **Simulator-only.** No physical Reachy Mini was driven; validation is MuJoCo
  (primary) + Webots (cross-engine). The *payment*, however, is real on-chain.
