# Reachy Mini Bridge

Payment-gated simulation bridge for the Hugging Face Reachy Mini robot (9 DOF).

## Architecture

```text
Zenoh robot/tunnel/action
  → Reachy Mini node (action receiver)
  → ReachyTaskPolicy FSM (SCANNING → TRACKING → EXPRESSIVE)
  → MuJoCo (official MJCF) + Webots (URDF PROTO, batch mode)
  → Zenoh robot/tunnel/result {status: "success"}
  → Zenoh robot/reachy_mini/metrics
```

## Structure

```text
mujoco_sim_bridge/
├── main.py                  # Bridge entrypoint (Zenoh listener)
├── policy/
│   └── controller.py        # ReachyTaskPolicy FSM (shared MuJoCo + Webots)
├── reachy_mini/
│   ├── node.py              # Action → simulation → metrics publisher
│   └── mapper.py            # Joint mapping (URDF ↔ MuJoCo)
├── simulation/
│   ├── environment.py       # MuJoCo env (official MJCF from reachy_mini pip)
│   ├── metrics.py           # Angular error tracker + FOV lock
│   ├── sim2sim.py           # Sim2SimValidator (MuJoCo + Webots, no fallback)
│   ├── webots_env.py        # DISABLED (raises RuntimeError if imported)
│   └── scenes/
│       ├── reachy_mini_simple.wbt          # Webots world (URDF model)
│       ├── protos/
│       │   ├── reachy_mini_simple.proto    # URDF-derived PROTO (9 DOF)
│       │   └── assets/                     # 41 STL meshes
│       └── controllers/
│           └── reachy_mini_controller/
│               └── reachy_mini_controller.py  # Webots native controller
└── requirements.txt
```

## Actuators (9 DOF)

| # | Joint | Range (rad) |
|---:|---|---|
| 0 | yaw_body | [-2.79, +2.79] |
| 1-6 | stewart_1..6 (neck) | varies per joint |
| 7-8 | right/left_antenna | [-0.80, +0.80] |

## Tests

```bash
# Local E2E (proxy + facilitator, no real funds)
python3 test_e2e_paid_action.py

# Live Base Sepolia E2E (needs PRIVATE_KEY env var)
python3 test_base_sepolia_tunnel_e2e.py

# Payment gate vs real Go binary
python3 test_payment_gate.py
```

## Requirements

- Python ≥ 3.10
- MuJoCo ≥ 3.0
- Webots ≥ R2023b (for sim2sim)
- `reachy_mini` pip package (official MJCF + URDF)
- zenoh Python bindings
