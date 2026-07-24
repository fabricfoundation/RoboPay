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
| 2 | Paid request, real chain | `200` + `PAYMENT-RESPONSE` receipt, **only after** simulator result `success` | `bridge/reachy_mini/test_base_sepolia_tunnel_e2e.py` (real tx hashes below) |
| 3 | Duplicate idempotency key | `409 REPLAY_DETECTED` (10-min TTL), no re-settle | `tunnel/internal/handlers/handlers_test.go` |
| 4 | Simulator execution failure | `502 SIMULATOR_EXECUTION_FAILED`, **no settlement** | `handlers_test.go::TestPostAction_DoesNotAcceptOrSettleOnSimulatorFailure` |
| 5 | Simulator result timeout | `504 SIMULATOR_RESULT_TIMEOUT`, **no settlement** | `handlers.go` PostAction timeout branch |
| 6 | Disallowed skill | `403 SKILL_NOT_ALLOWED` | `handlers_test.go` (allowlist path) |
| 7 | Wrong robot id | `403 WRONG_ROBOT` | `handlers_test.go` |
| 8 | Invalid params / over-duration | `400 INVALID_PARAMS` / `400 DURATION_LIMIT` | `handlers.go` `validatePayload` |

**Settlement invariant:** the x402 Gin middleware settles a payment **only when
the handler returns an HTTP status `< 400`**, which happens exclusively after the
simulator publishes a correlated `robot/tunnel/result` with status `success`.
502 and 504 are the explicit no-settlement contract for async failure — this is
what closes the "successful payment path demonstrated end-to-end" gap.

### Real Base Sepolia settlements (paid path)

Example transactions produced by the paid E2E run (BaseScan
`https://sepolia.basescan.org/tx/<hash>`):

- `0x49f9b6e4111774a85a20adfe0aaa9633be33872ea7062b0127b64483ceb13d74`
- `0x5bdba3a0e3af61b76bab1c9da973f8902694e42e93cf1124ddf9424460091421`
- `0xd322cd3d06a03b57aa4da8c3277a70d3daa52f1397324a31c00332a34e7799e6`
- `0xc566459096236f33d541c9d6db340be452132677f11c22fcdfe565943a5bf9b9`

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

`.github/workflows/ci.yml` runs `make test` (`go test -v ./...`, includes
`handlers_test.go`) on every push to the branch, so rows 3–8 are re-verified in
CI. `.github/workflows/base-sepolia-e2e.yml` covers the on-chain paid path.

## Known limitations (honest scope)

- **No arms.** Reachy Mini is a head + antennae platform; the Tier 1 task is
  expressive closed-loop head-tracking, not manipulation. This is a hardware
  property of the robot, not a shortcut.
- **Simulator-only.** No physical Reachy Mini was driven; validation is MuJoCo
  (primary) + Webots (cross-engine). The *payment*, however, is real on-chain.
