# sim-arm-01 · Tier 1 — MuJoCo RoboPay bridge

A pay-to-actuate bridge for **sim-arm-01**, a 2-DOF planar arm simulated in
MuJoCo. A paid `move_to_pose` action is verified, published to
`robot/tunnel/action`, executed by a closed-loop position servo, and reported
back as an `actionId`-correlated **terminal result** on `robot/tunnel/result`.
The relay settles payment **only** after consuming a `success` result — a
verified payment is not unconditional permission to move, and failures/timeouts
never settle.

```
relay.submit(action)                     robot node                     relay
   |  verify (402/400/409) BEFORE publish     |                            |
   |----------------------------------------->|                            |
   |  accepted / pending                      | run closed-loop servo      |
   |<-----------------------------------------|  produce metrics           |
   |                 robot/tunnel/result  <---| publish terminal result    |
   |  settle ONLY if status == success (correlated by actionId) ---------->|
```

## Skill

| skillId | params | price | description |
|---|---|---|---|
| `move_to_pose` | `{"target_qpos": [q1, q2]}` (radians) | 0.50 USDC | drive the arm to a target joint pose |

## Behavior

- **move_to_pose** — closed-loop servo drives joints to `target_qpos`, monitors
  real simulator state until the arm reaches the target (joint error < 0.03 rad)
  and has stopped moving (velocity < 0.05 rad/s) → `success`. A target outside
  the reachable ±3.14 rad range genuinely fails → `ACTION_FAILED` (no settlement).
- **unknown skill / malformed params** — `UNKNOWN_SKILL` / `INVALID_PARAMS`, no actuation.

State metrics: `joint_angles`, `joint_velocities`, `joint_error`, `success`,
`collision`, `steps_taken`.

## Reproduce (no ROS2 / no Zenoh router required)

```bash
pip install mujoco pybullet numpy pytest

# full pay-to-actuate transcript: 402 / 400 / 409 / success+settle / fail+no-settle
python -m sim_arm_01.flow.demo

# tests: flow, simulator, sim-to-sim
pytest test/ -v

# sim-to-sim: MuJoCo vs PyBullet convergence
python -m sim_arm_01.sim_to_sim
```

See [VALIDATION.md](VALIDATION.md) for expected output and how each review point
is addressed.

## Live Zenoh runtime (ROS2)

```bash
ros2 launch mujoco_bridge_sim_arm_01 bridge.launch.py
```

`sim_arm_01/node.py` is the live-Zenoh version of the same flow: it subscribes to
`robot/tunnel/action` and publishes the `actionId`-correlated terminal result to
`robot/tunnel/result`.

## Structure

```
sim_arm_01/simulator.py         headless MuJoCo 2-DOF arm + closed-loop servo
sim_arm_01/pybullet_simulator.py  PyBullet arm (same interface, for sim-to-sim)
sim_arm_01/mapper.py            action → joint target (no clamping; honest failures)
sim_arm_01/node.py              ROS2/Zenoh runtime: action → execute → terminal result
sim_arm_01/sim_to_sim.py        MuJoCo vs PyBullet convergence harness
sim_arm_01/flow/                transport-agnostic pay-to-actuate flow
  envelope.py                     action/result schema + payment-safety fields
  payment.py                      PaymentGuard (402 / tamper / replay / settle-on-success)
  executor.py                     skill dispatch → terminal ResultEnvelope
  relay.py                        relay + robot node + in-process bus
  demo.py                         reproducible end-to-end transcript
profiles/                       discoverable robot + skill + pricing profiles (5 YAMLs)
examples/example-envelopes.jsonc  real action/result envelopes
test/                           flow / simulator / sim-to-sim tests
VALIDATION.md                   reproducible evidence + review mapping
```
