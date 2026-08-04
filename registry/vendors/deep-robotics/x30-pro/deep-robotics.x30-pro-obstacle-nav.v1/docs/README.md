# Deep Robotics X30 Pro — RoboPay Integration (Tier 1)

Simulator-only Tier 1 submission connecting the Fabric RoboPay contract to a
MuJoCo simulation of the Deep Robotics X30 Pro quadruped, via the shared
Zenoh tunnel transport.

## Files

| File | Purpose |
|------|---------|
| `robot.profile.yaml` | Runtime, identity, and kinematic metadata for the X30 Pro simulator profile |
| `skills.yaml` | Discoverable, priced obstacle-navigation skill contract |
| `functions.yaml` | Agent-facing discovery, action, and status functions |
| `payment-policy.yaml` | Base Sepolia USDC policy and result-gated settlement rule |
| `execution-mapping.yaml` | Zenoh-to-MuJoCo mapping and completion semantics |
| `examples/action-envelope.obstacle-nav.json` | Non-production example envelope |
| `bridge/x30_pro_zenoh_bridge.py` | Fail-closed Zenoh/MuJoCo robot adapter |
| `simulation/scenes/x30_pro.xml` | X30 Pro MuJoCo scene (real actuators, obstacles, goal) |
| `simulation/runners/x30_pro_runner.py` | Episode runner: navigation policy + real trot gait + collision detection |
| `demo/run_demo.py` | End-to-end demo: unpaid → paid/success → replay-blocked → stop |
| `demo/run_failure_demo.py` | Deliberate-failure demo: timeout → no settlement |
| `tests/skill-contract.test.yaml` | Human-readable contract cases |
| `tests/test_bridge.py` | Executable parser, replay, result, and settlement-gate tests |
| `simulation/webots/worlds/x30_pro_obstacle_nav.wbt` | Webots world (proxy robot, same obstacle/goal layout) for Sim-to-Sim validation |
| `simulation/webots/controllers/x30_pro_navigation/x30_pro_navigation.py` | Webots controller running the identical navigation policy |
| `simulation/validation/validate_sim_to_sim.py` | Compares MuJoCo vs Webots outcome and writes a PASS/FAIL report |

## End-to-end architecture

```mermaid
sequenceDiagram
    autonumber
    participant P as Payer / agent
    participant F as Fabric relay + x402
    participant R as robotsdk tunnel bridge
    participant Z as Zenoh
    participant A as X30 Pro RoboPay bridge
    participant S as MuJoCo X30 Pro simulator

    P->>F: Discover robot, skill, and 0.002 USDC price
    P->>F: POST paid action
    F-->>P: accepted / pending + actionId
    F->>R: Verified, unsettled action envelope
    R->>Z: robot/tunnel/action
    Z->>A: Full normalized envelope
    A->>A: Validate + atomically claim replay key
    A->>S: run_episode(target_xy, max_episode_steps)
    S-->>A: episode metrics (status, displacement, path length, collisions)
    A->>Z: robot/tunnel/result correlated by actionId
    Z->>R: success / error
    R->>F: Structured result
    alt explicit success (goal_reached, zero collisions)
        F->>F: May settle
    else timeout, collision, invalid, duplicate, or unpaid
        F->>F: No settlement
    end
```

## Robot spec

| Parameter | Value |
|-----------|-------|
| Type | Quadruped |
| Mass | 59.0 kg |
| Standing height | 0.47 m |
| DOF | 12 (4 legs x HipX/HipY/Knee) |
| Forward vx | [0, 1.5] m/s |
| Backward vx | [-1.5, 0] m/s |
| Angular wz | [-1.0, 1.0] rad/s |
| Source reference | legubiao/quadruped_ros2_control (deep_robotics/x30_description) |

## Simulator model

The X30 Pro base moves on a planar (slide-x, slide-y, hinge-yaw) mount
driven every simulation step by a potential-field navigation policy
(velocity actuators). The four legs are independently position-actuated
every step with a live-computed trot gait (gait phase derived from
simulation time, not a pre-recorded animation). Obstacle collisions are
judged with MuJoCo's own narrow-phase contact detection against the base
geometry, not a proximity heuristic — the reported `collisions` metric
reflects genuine physical contact events, separate from the
`avoidance_events` counter (steering reactions while still clear of
contact).

Joint and link parameters (masses, inertias, offsets, joint limits) are
sourced from the official Deep Robotics X30 URDF
(`legubiao/quadruped_ros2_control`), using the `FL/FR/HL/HR` +
`HipX/HipY/Knee` naming convention rather than M20 Pro's `fl/fr/rl/rr` +
`hip/thigh/calf` convention.

This is a deliberate simplification of full quadruped whole-body balance
control (out of scope for a Tier 1 navigation skill): the base does not
rely on the legs for support, so we avoid needing a full standing/walking
balance controller while still exercising real per-step physics, real
actuation, and real collision detection for the obstacle-avoidance skill.

## Sim-to-Sim validation

The same potential-field navigation policy runs unmodified in two physics
engines -- MuJoCo (primary scene) and Webots (proxy robot body, same
obstacle layout and goal). This is a consistency check on the policy
itself, not a replication of the full leg model: the Webots side uses a
simple rigid body driven by the same policy code, not a 12-DOF quadruped.

```bash
python demo/run_demo.py   # writes docs/evidence/x30_pro_metrics.json
webots --mode=fast --batch simulation/webots/worlds/x30_pro_obstacle_nav.wbt
python simulation/validation/validate_sim_to_sim.py
```

Both engines must reach `goal_reached` with zero collisions, and
displacement/remaining-distance must agree within tolerance. Results are
written to `docs/evidence/sim_to_sim_validation.json`.

## Running locally

```bash
pip install -r tests/requirements.txt
python -m pytest tests/test_bridge.py -v
python demo/run_demo.py
python demo/run_failure_demo.py
```

## Result-gated settlement contract

Settlement is only eligible when:
- the episode status is `goal_reached`, and
- the collision count (real MuJoCo contacts against obstacles) is `0`.

Timeout, collision, unpaid, expired, malformed, and duplicate/replayed
actions never produce `settlementEligible: true`.
