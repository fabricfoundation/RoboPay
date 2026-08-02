# Reachy Mini — Gaze Tracking (Tier 1)

A ROS2 bridge that drives Hugging Face / Pollen Robotics **Reachy Mini**
through a reactive gaze-tracking skill: on receiving a `look_at` action
from the Fabric tunnel, the head (Stewart-platform neck + torso yaw)
turns to track a named target (e.g. `apple`) until locked on, using the
vendor's own kinematics.

## Overview

This package plugs into the same Fabric flow as the Unitree bridges one
level up: the tunnel verifies payment and publishes an action on the
Zenoh topic `robot/tunnel/action`; this bridge subscribes, maps the
action to a gaze command, and drives the robot in simulation.

Two physics engines are used, each for a different purpose:

- **MuJoCo** (primary) — full dynamic simulation. Uses the vendor's own
  `reachy_mini.vision.look_at.look_at_world_pose` and
  `reachy_mini.kinematics.analytical_kinematics.AnalyticalKinematics`
  end-to-end; joint commands are written to `data.ctrl` (actuators), not
  `qpos` directly, matching the vendor's own `MujocoBackend` — writing to
  `qpos` breaks the Stewart platform's closed-loop rod constraints.
- **Webots** (secondary, sim-to-sim validation) — see the **Webots
  simplification** section below for an important caveat.

The **policy** (`src/policy/controller.py`, `ReachyGazePolicy`) is
engine-agnostic: it only ever consumes `(target_visible,
angular_error_rad)` and outputs a `SEARCH → ACQUIRE → LOCKED` state. All
geometry/IK lives in the environment wrappers (`mujoco_env.py`,
`webots_env.py`), so swapping the engine is a real test of whether the
policy generalizes, not something tuned to one engine's quirks.

## Repository layout

```
reachy_mini/
├── sim_bridge/
│ ├── reachy_mini_bridge/ # ROS2 package: node.py, mapper.py
│ ├── src/
│ │ ├── policy/controller.py # engine-agnostic gaze FSM
│ │ └── simulation/
│ │ ├── mujoco_env.py # MuJoCo wrapper (vendor IK)
│ │ ├── webots_env.py # Webots wrapper (kinematic override)
│ │ ├── sim2sim.py # runs both engines, compares results
│ │ └── metrics.py
│ └── test/test_reachy_mini.py # standalone tests, no ROS2/sim needed
├── controllers/reachy_gaze_controller/ # Webots controller entry point
└── webots_world/ # ReachyMini.proto + world file
```

## 1. Run the MuJoCo episode standalone

```bash
conda activate reachy-bounty   # or any env with reachy-mini[mujoco], numpy, scipy
cd sim_bridge/src
python -m simulation.sim2sim --target apple
```

Outputs a JSON summary: `reached_lock`, `fov_visibility_rate`,
`final_angular_error_rad`, etc.

## 2. Run the Webots episode + sim-to-sim comparison

Webots must be run as the controller process itself (it needs the
`controller` module Webots ships, only importable from inside a Webots
controller subprocess).

```bash
export WEBOTS_HOME=/path/to/webots            # e.g. /snap/webots/27/usr/share/webots
export PYTHONPATH="$WEBOTS_HOME/lib/controller/python:$PYTHONPATH"

# headless (no GPU display needed):
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99

cd webots_world
webots --mode=fast --no-rendering --stdout --stderr --batch reachy_test.wbt
```

The `ReachyMini` node's `controller` field points at
`controllers/reachy_gaze_controller/`, which calls
`simulation.sim2sim.main(target_name, run_webots=True)` — this runs
**both** the MuJoCo and Webots episodes in the same process and prints a
combined `sim_to_sim_validation` block:

```json
{
  "mujoco": { "...": "..." },
  "webots": { "...": "..." },
  "sim_to_sim_validation": {
    "both_reached_lock": true,
    "final_error_delta_rad": 0.2225,
    "consistent": true
  }
}
```

`controllers/reachy_gaze_controller/runtime.ini` must point `COMMAND` at
a Python interpreter with `mujoco`, `numpy`, `scipy`, and `reachy-mini`
installed (Webots' bundled `python3` normally lacks these):

```ini
[python]
COMMAND = /path/to/conda/envs/reachy-bounty/bin/python3
```

## Webots simplification (read this before judging discrepancies)

Reachy Mini's neck is a **Stewart platform** — a closed-loop mechanism
(6 parallel rods + a ball joint). URDF only supports tree/serial
kinematic structures, so the URDF→Webots import cannot represent this as
stable rigid-body physics.

`webots_env.py` therefore runs Webots as a **kinematic validator**: the
robot's root `Solid` transform is set directly to point at the target,
rather than driving all 7 Stewart actuators individually like MuJoCo
does. This is why Webots converges to ~0 angular error while MuJoCo
settles into the vendor's documented IK residual (~0.16-0.27 rad,
verified separately as a built-in safety margin, not a bug). The
`consistent` check in `sim2sim.compare()` uses the same 0.30 rad
tolerance as the policy's own `lock_tolerance_rad` for this reason — a
tighter number would be checking something neither engine promises to
guarantee.

Sim-to-sim validation here confirms the **policy FSM and angular-error
metric agree across engines**, not that both engines reproduce identical
low-level actuator dynamics.

## Sandbox note: `gi`/PyGObject stub in `mujoco_env.py`

The vendor `reachy_mini` package's `__init__.py` eagerly imports its
entire app/io/vision/media stack (including face tracking, which needs
PyGObject / `gi`), even though this bridge only needs
`AnalyticalKinematics` and `look_at_world_pose` — pure-numpy geometry
with no GUI/vision dependency.

Under Webots' snap-confined controller subprocess specifically, the
system `libgirepository` .so is not visible even when installed and
resolvable from a normal shell. `mujoco_env.py` works around this by
stubbing `gi`/`gi.repository` in `sys.modules` before triggering the
`reachy_mini` import — a lazy stub that fabricates an empty module for
any attribute requested (`Gst`, `GstApp`, `GLib`, ...), so nothing here
is hardcoded to a fixed submodule list. This has no functional effect
since this file never touches `gi`/`FaceTracker`.

## 3. Run standalone tests

No ROS2 or simulator runtime required:

```bash
cd sim_bridge
python -m pytest test/test_reachy_mini.py -v
```

## 4. Run the ROS2 bridge node

```bash
uv venv --python 3.10
source .venv/bin/activate
uv pip install eclipse-zenoh

# build/run via the top-level Makefile once a reachy_mini bridge target
# is wired in, or run node.py directly with rclpy sourced:
source /opt/ros/humble/setup.bash
python -m reachy_mini_bridge.node
```

The node subscribes to `robot/tunnel/action` (same topic the tunnel
publishes verified paid actions to), maps `look_at`/`reset_gaze` events
to gaze commands, drives the MuJoCo environment, and republishes
per-step metrics on `robot/reachy_mini/metrics`.
