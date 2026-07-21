# Hugging Face Reachy Mini — MuJoCo Simulation Bridge (`bridge/reachy_mini`)

This package implements the **Fabric RoboPay simulation bridge (`mujoco_sim_bridge_reachy_mini`)** for the **Hugging Face Reachy Mini** robot.

---

## 🌟 Key Features

1. **Official Hugging Face / Pollen Robotics MJCF Scene**
   - Loads the official MuJoCo scene directly from the installed `reachy-mini[mujoco]` package (`descriptions/reachy_mini/mjcf/scenes/minimal.xml`).
   - Uses authentic 3D mesh assets, mass/inertia properties, and joint limits for the 6-DOF Stewart parallel neck platform and `yaw_body` torso rotation.

2. **Expressive Social Skill Execution (`object_tracking`)**
   - 4-phase Finite State Machine (FSM): `SCANNING` ➔ `TRACKING` ➔ `EXPRESSIVE` ➔ `DONE`.
   - Slew-rate limiting and exponential low-pass filtering on all actuators for smooth, realistic servo movement.
   - Celebration dance with antenna animation once target lock is confirmed.

3. **Sim-to-Sim Robustness Validation**
   - Automated `Sim2SimValidator` evaluates policy generalization across 3 randomized physical conditions (surface friction scale, object mass perturbation, alternate target objects).

4. **Zenoh Tunnel Integration**
   - Subscribes to `robot/tunnel/action` for `ActionEvent` payloads.
   - Publishes structured JSON telemetry to `robot/reachy_mini/metrics`.

---

## 📁 Repository Structure

```text
bridge/reachy_mini/
├── README.md
├── test_publisher.py               # Local Zenoh ActionEvent simulator
└── mujoco_sim_bridge/
    ├── main.py                     # Standalone bridge entrypoint
    ├── visualize.py                # 3D interactive real-time viewer
    ├── reachy_mini/
    │   ├── node.py                 # ReachyMiniBridgeNode (Zenoh pub/sub)
    │   └── mapper.py               # Action mapping
    └── src/
        ├── policy/
        │   └── controller.py       # ReachyTaskPolicy (4-phase FSM + motor filter)
        └── simulation/
            ├── environment.py      # ReachyMiniEnvironment (MuJoCo wrapper)
            ├── metrics.py          # SimulationMetricsTracker
            └── sim2sim.py          # Sim2SimValidator
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
    "overall_sim2sim_robustness_score": 1.0,
    "variation_details": [
      {
        "run_id": "sim2sim_variation_1_friction_noise",
        "target_object": "apple",
        "friction_scale": 1.07,
        "mass_scale": 0.819,
        "sim_duration_seconds": 3.01,
        "task_completed": true,
        "tracking_success_rate": 1.0,
        "success_rate_score": 1.0
      },
      {
        "run_id": "sim2sim_variation_2_mass_perturbation",
        "target_object": "croissant",
        "friction_scale": 1.02,
        "mass_scale": 0.94,
        "sim_duration_seconds": 3.01,
        "task_completed": true,
        "tracking_success_rate": 1.0,
        "success_rate_score": 1.0
      },
      {
        "run_id": "sim2sim_variation_3_duck_target",
        "target_object": "duck",
        "friction_scale": 1.0,
        "mass_scale": 1.0,
        "sim_duration_seconds": 3.01,
        "task_completed": true,
        "tracking_success_rate": 1.0,
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
