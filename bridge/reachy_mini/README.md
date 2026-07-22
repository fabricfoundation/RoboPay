# Hugging Face Reachy Mini — MuJoCo & Webots Simulation Bridge (`bridge/reachy_mini`)

A paid action on the tunnel's Zenoh topic starts an object-tracking episode on the official Hugging Face Reachy Mini model, validated in two simulators.

```
paid action (x402 / AIP) → tunnel → Zenoh robot/tunnel/action
→ subscriber → FSM (SCANNING → TRACKING → EXPRESSIVE) → yaw_body + Stewart neck
→ MuJoCo (official MJCF) / Webots R2023b (VRML scene + dedicated controller)
```

---

## Against the requirements

**Simulation only:** everything runs headless, no hardware SDKs. The robot has no arms — all actions are mapped to the expressive head-tracking skill (`object_tracking`).

**Approved simulators:** MuJoCo loads the official model from the installed `reachy-mini[mujoco]` package (`descriptions/reachy_mini/mjcf/scenes/minimal.xml`, 9 actuators: 1 `yaw_body` torso + 6 Stewart neck + 2 antennae). Webots R2023b runs the same policy via a dedicated controller in `scenes/controllers/reachy_mini_controller/` against a VRML scene with matching object layout (apple, croissant, duck, table).

**No replay:** the FSM closes the loop on live physics state — it reads `head_xmat` and `target_pos` from the simulator every step and computes a P-controller yaw command from scratch. No recorded trajectory anywhere in the codebase. `test_sim2sim.py` verifies both engines reach `task_completed: true` independently.

**Sim-to-sim:** `test_sim2sim.py` launches the real Webots binary (`--batch --mode=fast --no-rendering`) as a subprocess, runs the same FSM controller inside Webots physics, reads the result JSON produced by the controller, and compares against MuJoCo. The committed `scenes/reachy_mini_tabletop.wbt` uses the same object positions as the MuJoCo scene.

| target    | simulator | tracked | success_rate | min_error_rad | time    |
|-----------|-----------|---------|--------------|---------------|---------|
| apple     | MuJoCo    | yes     | 1.0          | 0.117         | 3.01 s  |
| croissant | MuJoCo    | yes     | 1.0          | 0.196         | 3.01 s  |
| duck      | MuJoCo    | yes     | 1.0          | 0.210         | 3.01 s  |
| apple     | Webots    | yes     | 1.0          | 0.340         | 4.00 s  |
| croissant | Webots    | yes     | 1.0          | 0.196         | 4.00 s  |
| duck      | Webots    | yes     | 1.0          | 0.473         | 4.00 s  |

Overall sim-to-sim robustness score: **1.0** (`simulation/webots_sim2sim_result.json`).

---

## RoboPay integration

Three tests exercise the payment integration:

**`test_payment_gate.py`** builds and runs the real tunnel binary against a local WebSocket proxy and asserts that an unpaid `POST /action` is rejected with HTTP 402 and x402 payment requirements.

**`test_link.py`** proves the tunnel's Zenoh session is live (`robot/config/<id>` put accepted), then publishes a paid-action event with the exact `handlers.PostAction` schema and asserts the robot runs the episode and returns metrics with `execution_status: SUCCESS`, `task_completed: true`, and `overall_sim2sim_robustness_score ≥ 0.9`. Both payment rails (x402 `POST /action` and AIP) publish to the same Zenoh topic, which is why the simulation subscribes there.

**`test_e2e_paid_action.py`** is the positive payment proof requested by the reviewer. It obtains the real 402 requirements through the proxy, builds a valid v2 `PAYMENT-SIGNATURE`, then sends the paid request through the real Tunnel binary. A deterministic local facilitator answers `/verify` and `/settle`; the resulting ActionEvent reaches Zenoh and the MuJoCo bridge returns metrics carrying the request id for correlation.

**`test_base_sepolia_tunnel_e2e.py`** is the optional live-network proof. It uses the official Python x402 client, sends the first unpaid request to the public Fabric endpoint, retries through the real compiled Tunnel, requires a successful settlement from `https://x402.org/facilitator`, prints the BaseScan transaction URL, and only then accepts correlated ROS2/MuJoCo metrics. The payer needs Base Sepolia USDC; never commit or share its private key.

```
test_payment_gate.py (requires tunnel binary):
  unpaid POST /action → 402 + payment requirements ✓
  malformed JSON      → 400                        ✓

test_link.py (requires bridge running):
  robot/config/<id> put → Zenoh session live        ✓
  paid ActionEvent      → metrics SUCCESS, score 1.0 ✓

test_e2e_paid_action.py (requires tunnel binary):
  paid HTTP POST /action → real Tunnel HTTP 200 ✓
  facilitator verify/settle → ActionEvent      ✓
  ActionEvent → correlated MuJoCo metrics      ✓
```

---

## Repository structure

```text
bridge/reachy_mini/
├── README.md
├── test_publisher.py                     # Zenoh ActionEvent simulator
├── test_payment_gate.py                  # x402 payment gate tests (needs tunnel binary)
├── test_link.py                          # end-to-end Zenoh paid action test
├── test_e2e_paid_action.py                # real Tunnel -> simulator proof
└── mujoco_sim_bridge/
    ├── main.py                           # Bridge entrypoint
    ├── visualize.py                      # 3D real-time viewer (MuJoCo)
    ├── reachy_mini/
    │   ├── node.py                       # Zenoh pub/sub node
    │   └── mapper.py                     # Action → task mapping
    ├── policy/
    │   └── controller.py                 # ReachyTaskPolicy (FSM + motor filter)
    └── simulation/
        ├── environment.py                # ReachyMiniEnvironment (MuJoCo)
        ├── webots_env.py                 # ReachyMiniWebotsEnvironment (fallback)
        ├── metrics.py                    # SimulationMetricsTracker
        ├── sim2sim.py                    # Sim2SimValidator (4 runs, 2 simulators)
        ├── test_sim2sim.py               # Sim-to-sim verification script
        └── scenes/
            └── reachy_mini_tabletop.wbt
            │   └── controllers/
            │       └── reachy_mini_controller/
            └── webots_sim2sim_result.json
```

---

## Reproduce

```bash
# Option A: ROS2 Humble (WSL Ubuntu-22.04 / colcon workspace)
cd RoboPay
source /opt/ros/humble/setup.bash
source .venv_ros2/bin/activate
make ROBOT=reachy_mini bridge-build
make ROBOT=reachy_mini bridge-run

# Option B: Standalone Python (Windows / macOS / Linux)
python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/main.py

# Terminal 2 — end-to-end link test (bridge must be running)
python RoboPay/bridge/reachy_mini/test_link.py

# Sim-to-sim test (runs real Webots subprocess + MuJoCo, needs Webots R2023b)
python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/simulation/test_sim2sim.py

# Build the real tunnel binary from the repository root first: make build
# Payment gate test (real Tunnel + local WebSocket proxy)
python RoboPay/bridge/reachy_mini/test_payment_gate.py

# Positive payment path using the ROS2 bridge above:
# HTTP -> real Tunnel -> Zenoh ActionEvent -> ROS2 node -> MuJoCo metrics
REACHY_BRIDGE_EXTERNAL=1 python RoboPay/bridge/reachy_mini/test_e2e_paid_action.py

# Live Base Sepolia proof (the ROS2 bridge must already be running):
export PRIVATE_KEY=0x...             # local only; never commit or send this
export ROBOT_ID=your-unique-robot-id
export ROBO_PAYEE_ADDRESS=0xYourReceivingWallet
python RoboPay/bridge/reachy_mini/test_base_sepolia_tunnel_e2e.py

# 3D viewer (MuJoCo)
python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/visualize.py

# Webots GUI — open scene and press Play
# RoboPay/bridge/reachy_mini/mujoco_sim_bridge/simulation/scenes/reachy_mini_tabletop.wbt
```

---

## Sample telemetry (`robot/reachy_mini/metrics`)

```json
{
  "robot_id": "reachy_mini_sim_01",
  "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
  "simulator": "MuJoCo",
  "task": "object_tracking",
  "execution_status": "SUCCESS",
  "sim_duration_seconds": 3.01,
  "steps_executed": 301,
  "phases_visited": ["EXPRESSIVE", "SCANNING", "TRACKING"],
  "metrics": {
    "head_tracking_error_rad": 0.381,
    "min_tracking_error_rad": 0.117,
    "tracking_success_count": 275,
    "tracking_success_rate": 1.0,
    "overall_fov_lock_rate": 0.914,
    "object_in_fov_seconds": 2.75,
    "task_completed": true,
    "success_rate_score": 1.0
  },
  "sim_to_sim_validation": {
    "num_variations_tested": 4,
    "simulators_evaluated": ["MuJoCo", "Webots"],
    "overall_sim2sim_robustness_score": 1.0
  }
}
```
