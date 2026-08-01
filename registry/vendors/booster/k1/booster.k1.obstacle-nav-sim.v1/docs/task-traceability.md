# Task Traceability

Maps each bounty requirement to the concrete evidence in this repo.

## Bounty requirements (booster-k1-tier-1)

| Requirement | Evidence |
|---|---|
| Connect RoboPay with a robot in simulation, action triggered by the policy | `bridge/booster_k1_zenoh_bridge.py` dispatches to `simulation/mujoco/runner.py`, which runs `simulation/common_policy/dwa_planner.py` (DWA local planner) in a closed loop -- not a scripted trajectory. See `docs/evidence/terminal/bridge-e2e-session.log` for a real run. |
| Simulator-only submission | MuJoCo 3.11.0 + Webots R2025a. No physical hardware referenced anywhere in this profile. |
| Approved simulator (Isaac Sim, Gazebo, MuJoCo, Webots, etc.) | MuJoCo and Webots both used. |
| Cannot simply replay a predefined animation/trajectory | The DWA planner (`dwa_planner.py::plan_step`) computes a fresh (v, omega) command every policy tick from the live robot pose and live obstacle positions read from the simulator. Changing scene parameters (obstacle position, mass, actuator gain) visibly changes the resulting trajectory -- this was observed directly during development (see git history: `348b84b`..`ca99bb1` shows several tuning iterations where trajectories changed in response to physics parameter changes, including one run that failed with `status=collision_detected` before tuning). |
| Cannot rely solely on built-in demo motions; must be triggered by policy/planner/controller | Triggered by `plan_step()`, a Dynamic Window Approach implementation, not a Webots/MuJoCo built-in demo. |
| Must provide simulator state metrics (target pose, grasping, door angle, collision status, path completion, etc.) | `simulation/mujoco/results/metrics.json` and `simulation/webots/results/metrics.json` report `distance_to_goal_m`, `path_length_m`, `collision_count`, `final_pose`, `status`, `sim_time_sec`, `physics_steps`, `policy_calls`, and a periodic `trajectory_sample`. All values are computed from live simulator state (`data.qpos`/`data.qvel` in MuJoCo, node translation/rotation fields in Webots), not hardcoded. |
| Must include Sim-to-Sim validation | `simulation/sim_to_sim_validate.py` runs both simulators' results through explicit tolerance checks and exits non-zero on disagreement. Verified PASSED: `distance_to_goal_m` differs by 0.0005m, `path_length_m` by 1.3%, both report `status=success` and `collision_count=0` on the identical scenario. |
| Example category: obstacle navigation | `k1_navigate_avoid_obstacles` skill: navigate to a goal pose while avoiding two static obstacles. |

## RoboPay integration gate (from prior review of a related submission)

These items were called out as missing in review of a related Booster
K1 submission (PR #16 / #29 on this repo) and are explicitly addressed
here:

| Concern raised | How it's addressed here |
|---|---|
| No demonstrated runtime path from a payment-verified ActionEvent through a bridge/adapter to simulator control | `bridge/booster_k1_zenoh_bridge.py::_on_action` is the single entry point: Zenoh subscription -> validation -> replay guard -> simulator dispatch -> result publish. Demonstrated live in `docs/evidence/terminal/bridge-e2e-session.log`. |
| Evidence/runtime for a different robot (G1) rather than the submitted robot (K1) | Every scene, controller, profile, and metrics file in this submission is K1-specific (`booster.k1.obstacle-nav-sim.v1`); no G1 or generic artifacts are included. |
| No complete robot registry profile | `robot.profile.yaml`, `skills.yaml`, `functions.yaml`, `execution-mapping.yaml`, `payment-policy.yaml` all present and cross-validated by `tests/test_profile.py` (12 tests). |
| Bridge does not publish a terminal result correlated to the original actionId | `make_result()` includes `actionId` in every published result; `demo/send_test_action.py` demonstrates the client receiving the correlated result. |
| Direct-Zenoh fallback used when Tunnel unavailable, unable to prove real payment verification | No fallback path exists in this bridge. `action_validator.py` requires `payment.verified == True` and `payment.status == "authorized"`; an unverified/pending payment is rejected before the simulator is ever invoked (`test_invalid_payment_rejected_without_dispatch`, and demonstrated live with `send_test_action.py --unpaid`). |
| Replay must not cause a second action | `replay_guard.py::check_and_reserve` is called and reserves the idempotency/actionId/authorizationId slot *before* the simulator is dispatched. Covered by 7 unit tests plus `test_replayed_action_rejected_without_second_dispatch` (asserts the simulator mock's `call_count == 1` after two identical requests), and demonstrated live with `send_test_action.py --replay-of`. |
| Settlement must be gated on actual result success | `payment-policy.yaml`'s `noSettleResultStatuses` excludes `success` (only) and requires `eligibleOnlyAfterResultStatus: success`. The bridge enforces this by construction: the published `status` field is `"success"` only when the simulator itself reports `status: success`; any other outcome (collision, timeout, subprocess failure) yields `status: error` (`test_simulator_reporting_failure_status_yields_error_result`, `test_simulator_failure_does_not_settle`). |

## Known limitations (stated explicitly, not hidden)

- The Booster K1 base is a geometric proxy (cylinder + planar joints), not
  an official CAD model -- Booster's CAD is not publicly available.
- `sim_time_sec` differs between MuJoCo (30.5s) and Webots (9.9s) for the
  same scenario, due to each engine's `basicTimeStep`/`timestep`
  resolution interacting differently with the policy's fixed-rate tick.
  Spatial metrics (`distance_to_goal_m`, `path_length_m`, `collision_count`)
  agree closely; wall/sim-clock time does not, and this is not hidden in
  `sim_to_sim_validate.py`'s tolerance set (`sim_time_sec` is intentionally
  not compared).
- The Zenoh replay guard is a local SQLite file per bridge instance; it
  is not distributed/clustered. Sufficient for a single-robot Tier 1
  submission, called out here for a multi-instance deployment context.
