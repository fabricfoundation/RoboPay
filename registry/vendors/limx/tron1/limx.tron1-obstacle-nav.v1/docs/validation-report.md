# LimX TRON1 — Tier 1 Validation Report

## Summary

This is a simulator-only Tier 1 submission for the TRON1 obstacle-avoidance
navigation skill. All evidence below was produced by running the included
demo scripts against the real MuJoCo scene at
`simulation/scenes/tron1.xml` -- no artifacts are reused from another
robot's profile.

## Test suite
$ python -m pytest tests/test_bridge.py -v
11 passed
Covers: successful settlement, unpaid rejection, invalid params-hash
rejection, expired-payment rejection, malformed envelope rejection, replay
rejection (no second motion), collision -> no settlement, timeout -> no
settlement, stop with no payment required, wrong robotId rejection, unknown
skillId rejection.

## Success run (`demo/run_demo.py`)

One `actionId` traced end-to-end: unpaid rejected (no simulator call) ->
paid request runs the real TRON1 MuJoCo episode -> replay of the same
`actionId` rejected with no second motion -> stop action succeeds with no
payment required.

Real simulator metrics from the paid run:

| Metric | Value |
|--------|-------|
| status | goal_reached |
| displacement_m | 7.6502 |
| path_length_m | 7.655 |
| collisions (real MuJoCo contacts) | 0 |
| avoidance_events | 1 |
| sim_steps | 5228 |
| sim_seconds | 10.454 |
| target_distance_remaining_m | 0.3498 |
| settlementEligible | true |

Terminal log: `docs/evidence/terminal_log.txt`
Raw metrics: `docs/evidence/tron1_metrics.json`

## Deliberate failure run (`demo/run_failure_demo.py`)

Same action contract, `max_episode_steps` deliberately set too low to reach
the goal, proving timeout produces no settlement:

| Metric | Value |
|--------|-------|
| status | error (episode_status:timeout) |
| sim_steps | 500 |
| target_distance_remaining_m | 7.445 |
| settlementEligible | **false** |

Terminal log: `docs/evidence/failure_terminal_log.txt`
Raw metrics: `docs/evidence/tron1_failure_metrics.json`

## Sim-to-Sim validation (MuJoCo vs Webots)

The same potential-field navigation policy runs in both MuJoCo (primary
scene) and Webots (proxy rigid body, identical obstacle/goal layout),
proving the skill is driven by policy logic rather than a
simulator-specific scripted trajectory.

| Check | Result |
|-------|--------|
| status_matches | PASS -- mujoco=goal_reached webots=goal_reached |
| zero_collisions_both_engines | PASS -- mujoco=0 webots=0 |
| displacement_within_tolerance | PASS -- mujoco=7.6502 webots=7.6527 diff=0.0025 |
| remaining_distance_within_tolerance | PASS -- mujoco=0.3498 webots=0.3473 diff=0.0025 |

Overall: **PASS**

Raw comparison: `docs/evidence/sim_to_sim_validation.json`
Webots run output: `simulation/webots/results/webots_metrics.json`

## Reviewer verification checklist

Before accepting this Tier 1 simulation submission, reviewers should confirm:

- the action targets the published `tron1_obstacle_navigation` skill at
  its listed `0.002 USDC` price;
- the envelope preserves `actionId`, `robotId`, `skillId`,
  `idempotencyKey`, `paramsHash`, and payment evidence;
- unpaid, invalid, expired, and replayed requests produce no simulator
  actuation (see `test_unpaid_action_is_rejected_before_actuation`,
  `test_invalid_params_hash_is_rejected`,
  `test_expired_payment_is_rejected`,
  `test_replay_of_same_action_id_causes_no_second_motion`);
- the bridge subscribes to `robot/tunnel/action` and publishes to
  `robot/tunnel/result` (not `robot/action`), preserving `actionId`
  end-to-end;
- the TRON1 MuJoCo scene (`simulation/scenes/tron1.xml`) defines
  actuators specific to this robot's wheeled-biped kinematics (2 x
  hip/knee/wheel + 3 base DOF), and
  `simulation/runners/tron1_runner.py` reads actuator names dynamically
  rather than hardcoding `ctrl` indices; metrics and logs are all produced
  by running this TRON1 scene, not reused from another robot's profile;
- the terminal `robot/tunnel/result` uses the same `actionId` as the
  originating action (see `docs/evidence/tron1_metrics.json`);
- failure or timeout in the simulator produces `settlementEligible: false`
  (see the deliberate failure run above);
- successful execution produces `settlementEligible: true` only after an
  explicit `goal_reached` terminal state with zero real (contact-detected)
  collisions -- not on a mid-episode/running state;
- replaying the same `actionId` causes no second simulator episode (see
  `test_replay_of_same_action_id_causes_no_second_motion` and Step 3 of
  `demo/run_demo.py`'s terminal log);
- the rolling gait applied to the wheels each step is computed live from
  commanded velocity (`_apply_leg_stance_and_wheels`), not a
  pre-recorded/replayed trajectory;
- Sim-to-Sim validation (MuJoCo vs Webots) passes all checks above;
- public evidence contains no wallet secrets or complete payment payloads.

## Known simplification (disclosed)

The TRON1 base is mounted on a planar (x, y, yaw) joint rather than a full
6-DOF free body balanced purely through leg/wheel-ground contact. This
avoids an unconstrained whole-body balance control problem that is out of
scope for a Tier 1 navigation skill, while preserving real per-step
physics integration, real actuation of all leg/wheel joints, and real
MuJoCo collision detection for the obstacle-avoidance objective. Full
standing/dynamic balance control could be added in a follow-up Tier 3
"custom skills" submission.
