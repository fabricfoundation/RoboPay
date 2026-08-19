# Deep Robotics M20 Pro — Tier 1 Validation Report

## Summary

This is a simulator-only Tier 1 submission for the M20 Pro obstacle-avoidance
navigation skill. All evidence below was produced by running the included
demo scripts against the real MuJoCo scene at
`simulation/scenes/m20_pro.xml` — no artifacts are reused from another
robot's profile.

## Test suite

**Bridge (Python):**
$ python -m pytest tests/test_bridge.py -v
10 passed
Covers: valid event dispatch, wrong-skill rejection, missing-params
rejection, malformed/incomplete event handling (dropped silently, no
crash), replay rejection (no second motion), simulator failure/timeout/
collision all yielding `status=error`, and stop.

**Tunnel (Go):**
$ cd tunnel && go test ./... -v
24 passed
Covers the fail-closed action gate (`TestPostAction_*`), the durable
idempotency store surviving a simulated restart
(`TestIdempotencyStore_PersistsAcrossReopen`), the verify-only x402
payment gate (`TestX402VerifyOnly_*`), the deferred-settlement execution
watcher (`TestExecutionWatcher_*`), and an end-to-end proof against a
recording facilitator (`TestE2E_*`) that settlement happens exactly once,
only after a genuine success result. See `docs/task-traceability.md` for
the full requirement-to-test mapping.

## Success run (`demo/run_demo.py`)

Payment verification now happens entirely in the Go tunnel before an
event ever reaches this bridge (see "Live Base Sepolia payment" below
for that proof). This demo exercises what remains at the bridge layer:
one `actionId` traced through a well-formed action → real M20 Pro
MuJoCo episode → replay of the same `actionId` rejected with no second
motion → stop action succeeds immediately.

Real simulator metrics from the dispatched run:

| Metric | Value |
|--------|-------|
| status | goal_reached |
| displacement_m | 7.6507 |
| path_length_m | 7.6511 |
| collisions (real MuJoCo contacts) | 0 |
| avoidance_events | 1 |
| sim_steps | 2935 |
| sim_seconds | 5.868 |
| target_distance_remaining_m | 0.3493 |
| settlementEligible | true |

Terminal log: `docs/evidence/terminal_log.txt`
Raw metrics: `docs/evidence/m20_pro_metrics.json`

## Deliberate failure run (`demo/run_failure_demo.py`)

Same action contract, `max_episode_steps` deliberately set too low to reach
the goal, proving timeout produces no settlement:

| Metric | Value |
|--------|-------|
| status | error (episode_status:timeout) |
| sim_steps | 50 |
| target_distance_remaining_m | 7.9727 |
| settlementEligible | **false** |

Terminal log: `docs/evidence/failure_terminal_log.txt`
Raw metrics: `docs/evidence/m20_pro_failure_metrics.json`


## Sim-to-Sim validation (MuJoCo vs Webots)

The same potential-field navigation policy runs in both MuJoCo (primary
scene) and Webots (proxy rigid body, identical obstacle/goal layout),
proving the skill is driven by policy logic rather than a
simulator-specific scripted trajectory.

| Check | Result |
|-------|--------|
| status_matches | PASS -- mujoco=goal_reached webots=goal_reached |
| zero_collisions_both_engines | PASS -- mujoco=0 webots=0 |
| displacement_within_tolerance | PASS -- mujoco=7.6507 webots=7.6518 diff=0.0011 |
| remaining_distance_within_tolerance | PASS -- mujoco=0.3493 webots=0.3482 diff=0.0011 |

Overall: **PASS**

Raw comparison: `docs/evidence/sim_to_sim_validation.json`
Webots run output: `simulation/webots/results/webots_metrics.json`

## Reviewer verification checklist

Before accepting this Tier 1 simulation submission, reviewers should confirm:

- the action targets the published `m20_pro_obstacle_navigation` skill at
  its listed `0.002 USDC` price;
- the envelope published by the tunnel to `robot/tunnel/action`
  carries `actionId`, `action`, and `params` -- no payment fields,
  since verification already happened in the tunnel before publish
  (see `execution-mapping.yaml`'s `envelope` section);
- unverified payment, malformed events, and replayed actionIds produce
  no simulator actuation -- unverified payment is rejected in the
  tunnel before any event is published (`TestX402VerifyOnly_*`,
  `TestE2E_ValidPaidAction_*`), and malformed/replayed events are
  rejected at the bridge (`test_malformed_json_is_dropped_silently_no_crash`,
  `test_event_missing_action_id_is_dropped_silently_no_crash`,
  `test_replayed_action_id_rejected_without_second_dispatch`);
- the bridge subscribes to `robot/tunnel/action` and publishes to
  `robot/tunnel/result` (not `robot/action`), preserving `actionId`
  end-to-end;
- the M20 Pro MuJoCo scene (`simulation/scenes/m20_pro.xml`) defines
  actuators specific to this robot's kinematics (12 leg joints + 3 base
  DOF), and `simulation/runners/m20_pro_runner.py` reads actuator names
  dynamically rather than hardcoding `ctrl` indices;
  metrics, video, and logs are all produced by running this M20 Pro scene,
  not reused from another robot's profile;
- the terminal `robot/tunnel/result` uses the same `actionId` as the
  originating action (see `docs/evidence/m20_pro_metrics.json`);
- failure or timeout in the simulator produces `settlementEligible: false`
  (see the deliberate failure run above);
- successful execution produces `settlementEligible: true` only after an
  explicit `goal_reached` terminal state with zero real (contact-detected)
  collisions — not on a mid-episode/running state;
- replaying the same `actionId` causes no second simulator episode (see
  `test_replayed_action_id_rejected_without_second_dispatch` and Step 3 of
  `demo/run_demo.py`'s terminal log);
- the gait applied to the legs each step is computed live from simulation
  time (`_apply_leg_gait`), not a pre-recorded/replayed trajectory;
- public evidence contains no wallet secrets or complete payment payloads.

## Live Base Sepolia payment

A real, wallet-signed, on-chain payment backs this submission --
`docs/evidence/base-sepolia/live-payment-e2e.md`:

| Field | Value |
|-------|-------|
| Transaction hash | `0x36000cc766fc95f7f1cfe8f2500a31cc98d236e98d050738553de555f1439587` |
| Status | SUCCESS |
| Block | 45531604 |
| Network | Base Sepolia (eip155:84532) |
| Amount | 2000 (smallest unit) = $0.002 USDC |

Verified independently via a Base Sepolia RPC (`receipt.status == 1`),
not just from the local terminal logs. Flow: `402` unsigned request →
client signs a real EIP-3009 `transferWithAuthorization` → tunnel
verifies against the real `x402.org` facilitator → `202 Accepted` →
real MuJoCo M20 Pro dispatch (`status=success`) →
`ExecutionWatcher` settles only after that success → real on-chain
USDC transfer. See `docs/task-traceability.md` for the full
requirement-to-code mapping.

## Known simplification (disclosed)

The M20 Pro base is mounted on a planar (x, y, yaw) joint rather than a
full 6-DOF free body balanced purely through leg-ground contact. This
avoids an unconstrained whole-body balance control problem that is out of
scope for a Tier 1 navigation skill, while preserving real per-step
physics integration, real actuation of all 12 leg joints via a live gait,
and real MuJoCo collision detection for the obstacle-avoidance objective.
Full standing/dynamic balance control could be added in a follow-up Tier 3
"custom skills" submission.
