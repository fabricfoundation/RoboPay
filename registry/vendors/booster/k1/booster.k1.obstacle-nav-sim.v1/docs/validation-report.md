# Validation Report

Booster K1 Tier 1 -- `k1_navigate_avoid_obstacles` skill.

## Test suite summary

| Layer | Tests | Status | Covers |
|---|---|---|---|
| Python -- `test_bridge.py` | 8 | PASSED | event parsing, wrong skill, bad params, replay, simulator failure/collision |
| Python -- `test_profile.py` | 12 | PASSED | registry YAML cross-consistency, envelope matches the tunnel's real schema |
| Python -- `test_replay_guard.py` | 7 | PASSED | bridge-local SQLite dedup (idempotencyKey/actionId/authorizationId) |
| Go -- `internal` (pre-existing) | 4 | PASSED | WS client dial/backoff, untouched by this submission |
| Go -- `internal/handlers` | 21 | PASSED | fail-closed allowlist, async 202/status contract, durable idempotency, verify-only gate, execution watcher |
| Go -- `cmd` (e2e) | 3 | PASSED | full router wiring against a recording facilitator: unpaid rejection, deferred settlement exactly-once, settlement-failure honesty |
| **Total** | **55** | **PASSED** | |

## MuJoCo run (real physics, not mocked)
python3 simulation/mujoco/runner.py --goal_x 5.0 --goal_y 0.0 --max_time_sec 60
| Metric | Value |
|---|---|
| status | success |
| distance_to_goal_m | 0.2979 |
| path_length_m | 5.4299 |
| collision_count | 0 |
| sim_time_sec | 30.5 |

Robot starts at (0, 0), navigates around two static obstacles
(cylinder at (2.5, 0), box at (1.2, 1.0)) to a goal at (5, 0).

## Webots run (real physics, independent engine, same policy code)

Extern-controller mode; see `docs/README.md` for the exact two-process
procedure.

| Metric | Value |
|---|---|
| status | success |
| distance_to_goal_m | 0.2984 |
| path_length_m | 5.3593 |
| collision_count | 0 |
| sim_time_sec | 9.9 |

## Sim-to-sim comparison
python3 simulation/sim_to_sim_validate.py --skip-run
| Metric | MuJoCo | Webots | Diff | Tolerance | Result |
|---|---|---|---|---|---|
| status | success | success | -- | exact match | PASS |
| distance_to_goal_m | 0.2979 | 0.2984 | 0.0005m | 0.15m abs | PASS |
| path_length_m | 5.4299 | 5.3593 | 1.3% | 15% rel | PASS |
| collision_count | 0 | 0 | 0 | exact match | PASS |

**Overall: PASSED.** `sim_time_sec` is intentionally excluded from
comparison -- see Limitations.

## Payment gate: verify-only, deferred settlement

Architecture (see `docs/README.md` for the full flow diagram):
POST /action --(X402VerifyOnly: verify, never settle)--> PostAction
--(fail-closed allowlist check, reserve actionId)--> robot/tunnel/action
--> bridge --> simulator --> robot/tunnel/result
--> ExecutionWatcher --(settle iff status=success)--> facilitator
`tunnel/cmd/e2e_test.go` wires the real gate + watcher against a
recording facilitator (no real network, analogous to the recording-
facilitator pattern used elsewhere in this repo's x402 test suites)
and proves:

| Scenario | Facilitator calls observed |
|---|---|
| Unpaid `POST /action` | `Verify`: 0, `Settle`: 0 (rejected 402 before reaching the facilitator at all) |
| Genuine success result | `Settle`: exactly 1, called only after the result arrived -- never at accept time |
| Failure result | `Settle`: 0 |
| Replayed success result (already settled) | `Settle`: 0 additional calls |
| Facilitator-side settlement failure | Recorded as `state=settlement_failed, settled=false` -- never silently upgraded to success |

## Limitations

- The K1 base is a geometric proxy (cylinder torso, planar
  slide+slide+hinge joints) in both simulators. This submission does
  not have access to Booster's official CAD/URDF. This is a
  simplification of the robot's geometry only -- the RoboPay
  integration, payment gate, and policy logic operate identically
  regardless of the geometry used.
- `sim_time_sec` differs between MuJoCo (30.5s) and Webots (9.9s) for
  the same scenario, because each engine's timestep resolution
  interacts differently with the policy's fixed-rate tick, not
  because the trajectories differ. `sim_to_sim_validate.py`
  deliberately does not compare this field.
- No live Base Sepolia transaction or real EVM-signed
  `PAYMENT-SIGNATURE` header is included -- this environment does not
  have wallet/signing credentials. The verify/settle separation --
  the actual behavior under scrutiny -- is instead proven directly
  against the production `ExecutionWatcher` and `IdempotencyStore`
  types using a recording facilitator, which observes and asserts on
  the exact same code path a real facilitator would exercise, minus
  the network call itself.
- The bridge-local `replay_guard.py` SQLite store is a secondary,
  single-process dedup layer. The tunnel's file-backed
  `IdempotencyStore` (survives a tunnel restart; see
  `TestIdempotencyStore_PersistsAcrossReopen`) is the authoritative
  replay guard for payment/settlement purposes.
- `sim_to_sim_validate.py --skip-run` requires both `results/metrics.json`
  files to already exist; the Webots leg cannot be auto-launched by
  the script because Webots' extern-controller mode requires two
  separate processes (see `docs/README.md`).
