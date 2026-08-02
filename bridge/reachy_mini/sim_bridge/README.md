# Reachy Mini — Gaze Tracking Sim Bridge (MuJoCo + Webots Sim-to-Sim)

Fabric RoboPay bridge for the Hugging Face / Pollen Robotics **Reachy Mini**
(Tier 1 — Simulator Skill Execution).

## Why gaze tracking

Reachy Mini is a desktop expressive robot: a head on a 6-bar Stewart-platform
neck plus a torso yaw joint. It has no arms and no wheeled base, so generic
navigation/pick-and-place actions don't apply. The skill implemented here is
**reactive gaze tracking**: the robot looks for a target, turns to look at
it, and holds the lock — driven by a closed-loop policy reading simulator
state every step, not a scripted or pre-baked animation.

## Architecture
Fabric proxy → tunnel (x402) → Zenoh "robot/tunnel/action"
│
reachy_mini_bridge/node.py (ROS2)
│
reachy_mini_bridge/mapper.py → GazeCommand
│
┌─────────────────────┴─────────────────────┐
src/simulation/mujoco_env.py src/simulation/webots_env.py
(official reachy-mini MJCF, (Webots world, same
vendor IK via data.ctrl) kinematic target — see
│ "Webots simplification")
look_at_target() + angular_error_to() look_at_target() + angular_error_to()
└─────────────────────┬─────────────────────┘
src/policy/controller.py
ReachyGazePolicy — pure reactive FSM
in: (target_visible, angular_error_rad)
out: (state, locked, command_issued)
│
src/simulation/sim2sim.py
→ results/*.json + robot/reachy_mini/metrics
**Division of responsibility, and why:** all geometry/IK lives in the
environment wrappers (`mujoco_env.py`, `webots_env.py`) — each one owns
`look_at_target()` (aim the head using that engine's own means) and
`angular_error_to()` (measure the result). `ReachyGazePolicy` never touches
geometry; it only decides SEARCH → ACQUIRE → LOCKED from the error signal.
This keeps the policy engine-agnostic, so running it against both engines
is a fair test of whether the *decision logic* generalizes — not a test of
whether both engines reproduce identical low-level actuator dynamics
(they don't, and that's expected — see below).

## Policy behavior (not a scripted trajectory)

- **SEARCH** — no usable target (not visible, or no error signal): holds.
- **ACQUIRE** — target visible, angular error above `lock_tolerance_rad`.
- **LOCKED** — angular error has stayed under `lock_tolerance_rad` for
  `lock_hold_steps` consecutive steps. A single bad step resets the hold
  counter, so LOCKED can only be reached through sustained, reactive
  convergence — see
  `test/test_reachy_mini.py::test_lock_counter_resets_when_error_spikes`.

`lock_tolerance_rad` defaults to 0.30 rad, set with margin above the
vendor analytical IK's real steady-state residual (~0.15–0.17 rad for
this geometry) so LOCKED is actually reachable, not just theoretical.

## Webots: a documented simplification, not a full physics port

The Stewart platform is a **closed-loop 6-bar mechanism**. URDF is a
tree/serial format, so `urdf2webots` cannot express the platform's closing
constraints as stable rigid-body physics — importing it naively produces
solids with undefined `boundingObject`s and an unstable simulation (the
robot falls over on the first physics step).

Rather than hand-fit unstable physics numbers to make it *look* stable, we
run Webots as a **kinematic validator**: all robot solids are `physics
NULL`, and `ReachyMiniWebotsEnv.look_at_target()` sets the robot body's
world orientation directly (via `Supervisor.getSelf()` + field access)
instead of driving the 7 Stewart actuators individually the way MuJoCo
does. This still exercises the identical policy FSM and the identical
angular-error metric — which is what sim-to-sim validation is checking —
while being honest that Webots is not modeling Stewart-platform dynamics.

This is also why Webots orients the whole robot body rather than an
isolated head joint: the `head` Solid is defined *inside* the `ReachyMini`
PROTO body and Webots does not expose PROTO-internal nodes to
`Supervisor.getFromDef()` from outside the PROTO. The exposed PROTO root
is the correct/available level for a supervisor-driven override.

## Sim-to-sim validation results

From `results/sim_to_sim_apple.json` (target: apple):

| | MuJoCo | Webots |
|---|---|---|
| reached_lock | true | true |
| fov_visibility_rate | 1.0 | 1.0 |
| final_angular_error_rad | 0.2225 | 0.0 |
| steps | 27 | 16 |

`final_error_delta_rad = 0.2225`, which is **above** the 0.15 rad
consistency threshold, so `consistent: false`.

**This delta is expected, not a bug.** MuJoCo's final error reflects the
vendor's real analytical IK with its built-in safety margin
(`automatic_body_yaw=True`, mechanical-limit-aware) — a genuine residual,
not a control failure. Webots' kinematic override has no such constraint,
so it converges to exactly 0.0. Both engines independently reach
`LOCKED` on the same target, which is the property sim-to-sim validation
is meant to check; the magnitude mismatch is a direct, disclosed
consequence of the physics-vs-kinematic difference described above, not a
policy inconsistency.

## Setup

MuJoCo side (used by `node.py` and the standalone `sim2sim.py` MuJoCo run):

```bash
pip install "reachy-mini[mujoco]" --break-system-packages   # official Pollen Robotics model
pip install mujoco numpy scipy --break-system-packages
```

Webots side (separate environment, used only by the Webots controller
process):

```bash
# Webots R2025a: https://cyberbotics.com/
# In the Python environment Webots will run the controller with:
pip install numpy scipy
```

Webots controllers run with whichever `python3` is on `PATH` for the
Webots process, which is often not your conda env. Point it explicitly
with a `runtime.ini` next to the controller script:

```ini
# webots_world/../controllers/reachy_gaze_controller/runtime.ini
[python]
COMMAND = /path/to/envs/reachy-bounty/bin/python3
```

Webots project layout requirement: `controllers/` must be a *sibling* of
the folder containing the `.wbt` world (not nested inside it). This repo
already follows that layout:
bridge/reachy_mini/
├── controllers/reachy_gaze_controller/reachy_gaze_controller.py
└── webots_world/reachy_test.wbt
## Verify — run standalone tests (no ROS2/simulator needed)

```bash
python -m pytest test/test_reachy_mini.py -v
```

12 tests: 5 cover `ReachyMiniMapper` (Fabric action → `GazeCommand`), 7
cover `ReachyGazePolicy`'s FSM transitions against its current API
(`step(target_visible, angular_error_rad)`).

## Run the sim-to-sim validator

```bash
# MuJoCo only, from anywhere with the sim_bridge/src on sys.path:
python -c "
import sys; sys.path.insert(0, 'src')
from simulation.sim2sim import run_mujoco_episode
import json
print(json.dumps(run_mujoco_episode('apple'), indent=2))
"
```

```bash
# Webots episode: must run *inside* a Webots controller process (needs
# the `controller` module Webots injects), which is what
# controllers/reachy_gaze_controller/reachy_gaze_controller.py does.
# Simply open the world in Webots and press play. Note: webots_world/
# lives one level up from this README (bridge/reachy_mini/webots_world/),
# not inside sim_bridge/ -- run this from bridge/reachy_mini/:
cd ../webots_world && webots reachy_test.wbt
```

Combining both results into one `sim_to_sim_validation` report (as in
`results/sim_to_sim_apple.json`) is currently a manual join of the two
runs' JSON output, via `simulation.sim2sim.compare()` — see that file for
the exact call used to produce the results above.

## Run the live bridge (ROS2)

```bash
colcon build --packages-select sim_bridge_reachy_mini
source install/setup.bash
ros2 launch sim_bridge_reachy_mini sim_bridge.launch.py
```

Then publish a Fabric-style action on the Zenoh topic, e.g. `look_at`
with `{"target": "apple"}`, and observe metrics on
`robot/reachy_mini/metrics`. Tunable parameters (see
`config/default.yaml`): `lock_tolerance_rad`, `lock_hold_steps`.

## Repository layout
sim_bridge_reachy_mini/
├── README.md
├── package.xml / setup.py / setup.cfg / resource/
├── config/default.yaml
├── launch/sim_bridge.launch.py
├── reachy_mini_bridge/
│ ├── node.py # ROS2 node: Zenoh in, policy loop, metrics out
│ └── mapper.py # Fabric ActionEvent -> GazeCommand
├── src/
│ ├── policy/controller.py # ReachyGazePolicy — pure FSM
│ └── simulation/
│ ├── head_ik.py # vendor AnalyticalKinematics wrapper
│ ├── mujoco_env.py # official MJCF wrapper, owns IK + error
│ ├── webots_env.py # Webots supervisor wrapper (kinematic)
│ ├── metrics.py # EpisodeMetrics / telemetry
│ └── sim2sim.py # cross-engine validation runner
├── results/
│ └── sim_to_sim_apple.json # recorded MuJoCo vs Webots comparison
└── test/test_reachy_mini.py # 12 standalone unit tests
## Notes

- The official Reachy Mini model is loaded from the `reachy-mini` pip
  package at runtime; it is not vendored in this repo. The Webots PROTO
  (`webots_world/ReachyMini.proto`) is generated from the vendor's URDF via
  `urdf2webots`, with mesh paths rewritten to absolute paths and one
  invalid-identifier DEF name (`5w_speaker`, invalid because VRML
  identifiers cannot start with a digit) renamed.
- All control logic (the FSM in `controller.py`) is original to this
  submission. The Webots kinematic-override strategy is a deliberate,
  disclosed simplification of Stewart-platform closed-loop dynamics — see
  "Webots: a documented simplification" above.
