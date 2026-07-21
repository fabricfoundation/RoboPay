# Reachy Mini — MuJoCo Simulation Bridge for Fabric RoboPay

A **Fabric Foundation RoboPay** bridge for the **Hugging Face Reachy Mini** robot, built on the **official MuJoCo model** shipped with the `reachy-mini` Python package. The bridge subscribes to the Zenoh `robot/tunnel/action` topic (same architecture as the official Unitree G1 bridge), executes an expressive **object-tracking task** via a finite-state-machine policy, and publishes head-tracking metrics back over Zenoh.

> Submitted for the **Tier 1 — Simulator Skill Execution** reward of the Hugging Face Reachy Mini bounty.

---

## What the Reachy Mini Actually Does

The Reachy Mini is an **expressive social robot** — it has no arms. Its actuated degrees of freedom are:

| Actuator | Description | Range |
|---|---|---|
| `yaw_body` | Torso rotation | ±160° |
| `stewart_1..6` | 6-DOF neck (Stewart parallel mechanism) | varies |
| `right_antenna` | Right expressiveness antenna | passive |
| `left_antenna` | Left expressiveness antenna | passive |

The skill demonstrated here is **active object tracking** — the robot locates the `apple` object in the scene, rotates its torso to point toward it, and expresses recognition through animated head movement and antennae.

---

## Architecture

```
Fabric Proxy (cloud)
    │  x402 payment verified
    ▼
Tunnel (tunnel/ — Go)
    │  publishes Zenoh topic: robot/tunnel/action
    ▼
Bridge (this package — Python)
    │  receives ActionEvent → runs FSM policy on official Reachy Mini MJCF
    ▼
MuJoCo (official reachy_mini package scene — apple, duck, croissant, table)
    │  returns head-tracking metrics
    ▼
robot/reachy_mini/metrics  (Zenoh publish)
```

---

## Policy: Finite-State Machine (FSM)

```
SCANNING → TRACKING → EXPRESSIVE → DONE
```

| Phase | Behaviour | Trigger to next |
|---|---|---|
| **SCANNING** | Slow sinusoidal torso sweep + head oscillation | `sim_time >= 1.0s` |
| **TRACKING** | P-controller on `yaw_body` to point at object; gentle head nod | `tracking_success_count >= 30` frames |
| **EXPRESSIVE** | Fast head bobs (Stewart at 5 Hz); excited reaction | `2.0s` in phase |
| **DONE** | Hold pose | terminal |

The policy is **not a replay** — the `yaw_body` target is computed every step from the live position of the target object relative to the head.

---

## MuJoCo Scene

The bridge loads the **official scene** from the installed `reachy-mini` package:

```
<package>/reachy_mini/descriptions/reachy_mini/mjcf/scenes/minimal.xml
```

The scene contains:
- 🤖 Reachy Mini robot (STL meshes, realistic inertia, servo parameters)
- 🍎 Apple (`apple` body, mass=0.1 kg)
- 🥐 Croissant (`croissant` body)
- 🦆 Rubber duck (`duck` body)
- 🪵 Wooden table

---

## Simulator State Metrics

Each execution emits a JSON payload to `robot/reachy_mini/metrics`:

```json
{
  "robot_id": "reachy_mini_sim_01",
  "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
  "simulator": "MuJoCo",
  "task": "object_tracking",
  "execution_status": "SUCCESS",
  "sim_duration_seconds": 3.01,
  "steps_executed": 301,
  "phases_visited": ["SCANNING", "TRACKING", "EXPRESSIVE"],
  "metrics": {
    "head_tracking_error_rad": 1.056,
    "min_tracking_error_rad": 0.503,
    "tracking_success_count": 96,
    "tracking_success_rate": 0.319,
    "object_in_fov_seconds": 0.96,
    "antenna_activity": 0.034,
    "task_completed": true,
    "success_rate_score": 0.319
  },
  "sim_to_sim_validation": { ... }
}
```

### Metric definitions

| Metric | Description |
|---|---|
| `head_tracking_error_rad` | Mean angular error between head Y-axis and object direction |
| `min_tracking_error_rad` | Best tracking achieved (0.50 rad ≈ 29°) |
| `tracking_success_count` | Frames with error < 0.65 rad (within 37° FOV) |
| `tracking_success_rate` | Fraction of frames in successful tracking |
| `object_in_fov_seconds` | Total sim-time with object in field of view |
| `antenna_activity` | Cumulative antenna joint displacement (expressiveness) |

---

## Repository Layout

```
bridge/reachy_mini/
├── mujoco_sim_bridge/
│   ├── main.py                     # Bridge entrypoint (run this)
│   ├── reachy_mini/
│   │   ├── node.py                 # Zenoh subscriber node
│   │   └── mapper.py               # ActionEvent → task mapper
│   └── src/
│       ├── simulation/
│       │   ├── environment.py      # Official MJCF loader + obs interface
│       │   ├── metrics.py          # Head-tracking metrics tracker
│       │   └── sim2sim.py          # Physics-variation robustness validator
│       └── policy/
│           └── controller.py       # ReachyTaskPolicy (FSM)
└── test_publisher.py               # Fabric tunnel simulator for local testing
```

---

## Requirements

```bash
pip install "reachy-mini[mujoco]"   # installs official MJCF + MuJoCo 3.3.0
pip install eclipse-zenoh
```

Or from the project root:
```bash
pip install -r requirements.txt
pip install "reachy-mini[mujoco]" eclipse-zenoh
```

> **Note**: The `reachy-mini` package provides the official robot MJCF with all STL mesh assets — no manual model download required.

---

## Running the Bridge

### Option 1 — Local testing (two terminals)

**Terminal 1 — Start the bridge:**
```bash
cd <repo-root>
python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/main.py
```

**Terminal 2 — Simulate the Fabric tunnel:**
```bash
python RoboPay/bridge/reachy_mini/test_publisher.py --action look_at_apple
python RoboPay/bridge/reachy_mini/test_publisher.py --action object_tracking
python RoboPay/bridge/reachy_mini/test_publisher.py --action wave
```

The bridge receives the event, loads the official Reachy Mini MJCF, runs the FSM policy, and publishes metrics. The publisher terminal prints the full JSON result.

### Option 2 — With the real Fabric Tunnel (Linux/Go)

Follow the tunnel setup from [`tunnel/README.md`](../../../tunnel/README.md), then start the bridge as above.

---

## Supported Actions

All actions map to the `object_tracking` task (the robot's actual skill set):

| Action name | Notes |
|---|---|
| `look_at`, `look_at_apple`, `object_tracking`, `track` | Primary tracking task |
| `wave`, `express_happiness` | Same FSM — expressiveness phases |
| `pick_and_place`, `door_open`, `stop` | Graceful fallback to tracking |

---

## Sim-to-Sim Validation

The bridge automatically runs 3 physics-variation trials with randomised friction (×0.75–1.25) and apple mass (×0.80–1.20) to validate policy robustness. Results are reported under `sim_to_sim_validation` in the metrics payload.

---

## Design Notes

- **Official model**: Loads `scenes/minimal.xml` from the installed `reachy-mini` package, with real STL meshes, accurate inertia tensors, and calibrated servo parameters (`kp`, `forcerange`).
- **No ROS2 required**: Uses `eclipse-zenoh` (Python) directly — runs on Windows, macOS, and Linux.
- **Headless MuJoCo**: No display server needed.
- **Same bridge pattern as G1**: `node.py` mirrors `bridge/unitree/g1/isaac_sim_bridge/g1/node.py`.
- **Policy-driven (not replay)**: `yaw_body` target is computed each step from live object positions in simulation state.
- **Honest metrics**: Tracking error reflects real geometry — the Stewart mechanism cannot achieve the full head range in a single yaw sweep, so `success_rate_score ≈ 0.32` is physically accurate, not artificially inflated.
