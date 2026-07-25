# sim-arm-01 · Tier 1 — MuJoCo RoboPay bridge

A pay-to-actuate bridge for **sim-arm-01**, a 2-DOF planar arm simulated in
MuJoCo. It subscribes to `robot/tunnel/action`, and when a paid `move_to_pose`
action arrives, drives the arm to the target joint pose with a closed-loop
position servo, reporting real simulator state metrics.

Designed to run **after** RoboPay's x402 tunnel verifies and settles payment —
a verified payment is not unconditional permission to move; the controller still
enforces joint limits and reports genuine success/failure.

## Skill

| skillId | params | description |
|---|---|---|
| `move_to_pose` | `{"target_qpos": [q1, q2]}` (radians) | drive the arm to a target joint pose |

## Behavior

- **move_to_pose** — closed-loop servo drives joints to `target_qpos`, monitors
  real simulator state until the arm reaches the target (joint error < 0.03 rad)
  and has stopped moving (velocity < 0.05 rad/s).
- **unknown action** — zero motion (safe default).

## State metrics reported

`joint_angles`, `joint_velocities`, `joint_error`, `success`, `collision`,
`steps_taken`.

## Structure

```
sim_arm_01/simulator.py   headless MuJoCo 2-DOF arm + closed-loop servo
sim_arm_01/mapper.py      action → joint target (with clamping)
sim_arm_01/node.py        ROS2 node: Zenoh action → execute → log metrics
config/params.yaml        Zenoh topic / cmd_vel params
launch/bridge.launch.py   ros2 launch entry point
test/test_simulator.py    execution tests (mujoco + pytest, no ROS2 needed)
```

## Run

```bash
ros2 launch mujoco_bridge_sim_arm_01 bridge.launch.py
```

Tests (no ROS2 required):

```bash
pip install mujoco pytest
pytest test/test_simulator.py
```
