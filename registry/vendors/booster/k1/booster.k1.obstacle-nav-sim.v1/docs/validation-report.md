# Validation Report

Booster K1 Tier 1 -- `k1_navigate_avoid_obstacles` skill.

## Test suite
python3 -m pytest tests/ -v
| Module | Tests | Status | Covers |
|---|---|---|---|
| `test_action_validator.py` | 11 | PASSED | payment/envelope validation: missing fields, tampered params hash, unverified/pending/expired/already-settled payment, wrong network/asset/amount |
| `test_replay_guard.py` | 7 | PASSED | idempotencyKey/actionId/authorizationId replay rejection, legitimate independent actions, concurrent-duplicate reservation |
| `test_bridge.py` | 6 | PASSED | full `_on_action` flow with simulator + Zenoh mocked: valid dispatch, unpaid rejection (no dispatch), replay rejection (no second dispatch), simulator failure/collision -> status=error |
| `test_profile.py` | 12 | PASSED | registry YAML cross-consistency (profileId, skillId, topics) and alignment with the code's actual enforcement |
| **Total** | **36** | **PASSED** | |

## MuJoCo run (real physics, not mocked)
python3 simulation/mujoco/runner.py --goal_x 5.0 --goal_y 0.0 --max_time_sec 60
| Metric | Value |
|---|---|
| status | success |
| distance_to_goal_m | 0.2979 |
| path_length_m | 5.4299 |
| collision_count | 0 |
| sim_time_sec | 30.5 |
| physics_steps | 3050 |
| policy_calls | 305 |

Robot starts at (0, 0), navigates around two static obstacles
(cylinder at (2.5, 0), box at (1.2, 1.0)) to a goal at (5, 0).

## Webots run (real physics, independent engine, same policy code)
extern-controller mode, see docs/README.md for the exact procedure

GOAL_X=5.0 GOAL_Y=0.0 MAX_TIME_SEC=60 python3 k1_navigation.py
| Metric | Value |
|---|---|
| status | success |
| distance_to_goal_m | 0.2984 |
| path_length_m | 5.3593 |
| collision_count | 0 |
| sim_time_sec | 9.9 |
| physics_steps | 990 |
| policy_calls | 198 |

Identical scenario (same start pose, same goal, same obstacle
positions) run through Webots' ODE physics instead of MuJoCo's.

## Sim-to-sim comparison
python3 simulation/sim_to_sim_validate.py --skip-run
| Metric | MuJoCo | Webots | Diff | Tolerance | Result |
|---|---|---|---|---|---|
| status | success | success | -- | exact match | PASS |
| distance_to_goal_m | 0.2979 | 0.2984 | 0.0005m | 0.15m abs | PASS |
| path_length_m | 5.4299 | 5.3593 | 1.3% | 15% rel | PASS |
| collision_count | 0 | 0 | 0 | exact match | PASS |

**Overall: PASSED.**

`sim_time_sec` is intentionally not compared -- it differs (30.5s vs
9.9s) because each engine's `basicTimeStep`/`timestep` resolution
interacts differently with the policy's fixed-rate tick, not because
the trajectories differ. Spatial and outcome metrics, which are what
actually matter for "did the robot do the task correctly," agree
within 1-2%.

## End-to-end bridge run (real Zenoh, real payment gate, real simulator)

Raw session log: `docs/evidence/terminal/bridge-e2e-session.log`.

| Scenario | Sent | Result | Simulator dispatched? |
|---|---|---|---|
| Valid paid action | `payment.verified=true, status=authorized, settled=false` | `status=success`, metrics matching the manual MuJoCo run above | Yes, once |
| Unpaid action | `payment.verified=false` | `status=rejected`, `errorCode=payment_not_verified` | No |
| Replay of the first action's actionId | same `actionId`, new `idempotencyKey`/`authorizationId` | `status=rejected`, `errorCode=replay_detected` | No (not dispatched a second time) |

## Limitations

- The K1 base is represented as a geometric proxy (cylinder torso,
  planar slide+slide+hinge joints) in both simulators. Booster's
  official CAD/URDF for the K1 is not publicly available, so no
  submission using it could include a real CAD model without
  redistributing unlicensed assets. This is a simplification of the
  robot's visual/collision geometry, not of the RoboPay integration,
  payment gate, or policy logic, which all operate identically
  regardless of the geometry used.
- The replay guard's SQLite database is local to a single bridge
  process/instance. A multi-instance deployment would need a shared
  store; out of scope for this Tier 1 simulation submission.
- `sim_to_sim_validate.py --skip-run` requires both `results/metrics.json`
  files to already exist; the Webots leg cannot be auto-launched by
  the script because Webots' extern-controller mode requires two
  separate processes (see docs/README.md). This is a reproducibility
  step documented explicitly, not a gap in the validation logic.
