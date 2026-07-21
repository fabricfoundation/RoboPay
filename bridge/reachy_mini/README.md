# Hugging Face Reachy Mini — MuJoCo & Webots Simulation Bridge (`bridge/reachy_mini`)

This directory contains the **Fabric RoboPay simulation bridge (`mujoco_sim_bridge_reachy_mini`)** for the **Hugging Face Reachy Mini** social robot.

Submitted for the **Hugging Face Reachy Mini Tier 1 — Simulator Skill Execution Bounty**.

---

## 🌟 Key Features

1. **Official Hugging Face / Pollen Robotics Models**
   - **MuJoCo:** Loads official MJCF scene directly from installed `reachy-mini[mujoco]` package (`descriptions/reachy_mini/mjcf/scenes/minimal.xml`).
   - **Webots:** Includes a VRML scene (`webots_project/worlds/reachy_mini_tabletop.wbt`) with matching tabletop layout (apple, croissant, duck, table).

2. **Expressive Social Skill Execution (`object_tracking`)**
   - Implements a smooth 4-phase Finite State Machine (FSM): `SCANNING` ➔ `TRACKING` ➔ `EXPRESSIVE` ➔ `DONE`.
   - Slew-rate limiting and low-pass filtering ensure realistic servo motor dynamics (no jitter or discontinuous motion).
   - Celebration dance with antenna animation once target is locked.

3. **Dual-Engine Cross-Simulator Sim-to-Sim Validation (`MuJoCo` ➔ `Webots`)**
   - Automated multi-simulator runner (`sim2sim.py`) evaluates policy generalization across **both MuJoCo and Webots physics engines**:
     - **Run 1 (MuJoCo):** Randomized friction and mass perturbations.
     - **Run 2 (Webots):** Real Webots physics engine execution across 3 target objects (`apple`, `croissant`, `duck`).
     - **Run 3 (MuJoCo):** Multi-target tracking verification.

4. **Fabric Zenoh Tunnel Integration**
   - Subscribes to `robot/tunnel/action` for `ActionEvent` messages.
   - Publishes structured JSON telemetry to `robot/reachy_mini/metrics`.

---

## 📁 Repository Structure

```text
bridge/reachy_mini/
├── README.md
├── test_publisher.py               # Local Zenoh ActionEvent simulator
└── mujoco_sim_bridge/
    ├── main.py                     # Standalone bridge entrypoint
    ├── visualize.py                # 3D interactive real-time visualizer
    ├── reachy_mini/
    │   ├── node.py                 # ReachyMiniBridgeNode (Zenoh pub/sub)
    │   └── mapper.py               # Action mapping (look_at, wave, track -> object_tracking)
    └── src/
        ├── policy/
        │   └── controller.py       # ReachyTaskPolicy (4-phase FSM + motor filter)
        └── simulation/
            ├── environment.py      # ReachyMiniEnvironment (MuJoCo wrapper)
            ├── webots_env.py       # ReachyMiniWebotsEnvironment
            ├── metrics.py          # SimulationMetricsTracker (angular error, FOV lock)
            ├── sim2sim.py          # Sim2SimValidator (MuJoCo + Webots cross-validation)
            └── webots_project/
                ├── worlds/reachy_mini_tabletop.wbt
                └── controllers/reachy_mini_controller/
```

---

## 📊 Sample Telemetry Payload (`robot/reachy_mini/metrics`)

```json
{
  "robot_id": "reachy_mini_sim_01",
  "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
  "simulator": "MuJoCo",
  "task": "object_tracking",
  "execution_status": "SUCCESS",
  "sim_duration_seconds": 3.01,
  "steps_executed": 301,
  "phases_visited": [
    "EXPRESSIVE",
    "SCANNING",
    "TRACKING"
  ],
  "metrics": {
    "head_tracking_error_rad": 0.3808,
    "min_tracking_error_rad": 0.1172,
    "tracking_success_count": 275,
    "tracking_success_rate": 1.0,
    "overall_fov_lock_rate": 0.914,
    "object_in_fov_seconds": 2.75,
    "antenna_activity": 0.034,
    "task_completed": true,
    "success_rate_score": 1.0
  },
  "sim_to_sim_validation": {
    "num_variations_tested": 3,
    "simulators_evaluated": [
      "MuJoCo",
      "Webots"
    ],
    "overall_sim2sim_robustness_score": 1.0,
    "variation_details": [
      {
        "run_id": "sim2sim_run_1_mujoco_friction",
        "simulator_engine": "MuJoCo",
        "target_object": "apple",
        "friction_scale": 1.07,
        "mass_scale": 0.819,
        "task_completed": true,
        "success_rate_score": 1.0
      },
      {
        "run_id": "sim2sim_run_2_webots_cross_engine",
        "simulator_engine": "Webots",
        "targets_tracked": [
          "apple",
          "croissant",
          "duck"
        ],
        "phases_visited": [
          "EXPRESSIVE",
          "SCANNING",
          "TRACKING"
        ],
        "sim_duration_seconds": 10.5,
        "task_completed": true,
        "tracking_success_rate": 1.0,
        "success_rate_score": 1.0,
        "per_target": [
          { "target_object": "apple", "tracking_success_rate": 1.0 },
          { "target_object": "croissant", "tracking_success_rate": 1.0 },
          { "target_object": "duck", "tracking_success_rate": 1.0 }
        ]
      },
      {
        "run_id": "sim2sim_run_3_mujoco_duck_target",
        "simulator_engine": "MuJoCo",
        "target_object": "duck",
        "task_completed": true,
        "success_rate_score": 1.0
      }
    ]
  }
}
```

---

## 🚀 How to Run

### 1. Run Standalone Bridge (Terminal 1)
```bash
python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/main.py
```

### 2. Send Test Action via Zenoh (Terminal 2)
```bash
python RoboPay/bridge/reachy_mini/test_publisher.py --action look_at_apple
```

### 3. Launch 3D Real-Time Visualizer
```bash
python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/visualize.py
```
