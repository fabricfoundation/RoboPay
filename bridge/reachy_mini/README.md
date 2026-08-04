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
│   ├── node.py              # Action → simulation → metrics publisher (rejects unregistered actions)
│   └── mapper.py            # Registered action → task map (stop → safe_stop; unknown → rejected)
├── simulation/
│   ├── environment.py       # MuJoCo env (official MJCF from reachy_mini pip)
│   ├── metrics.py           # Angular error tracker + FOV lock
│   ├── sim2sim.py           # Sim2SimValidator (MuJoCo + Webots, no fallback)
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

## Runtime configuration

The bridge uses documented defaults but does not require source edits for a
different deployment:

| Variable | Default | Purpose |
|---|---|---|
| `ROBOT_ID` | `reachy-mini-kauker` | Exact robot identity required on every Tunnel-verified event |
| `ZENOH_ENDPOINT` | `tcp/127.0.0.1:7447` | Simple peer endpoint |
| `ZENOH_CONFIG` | unset | Full Zenoh JSON5 config; takes precedence over the endpoint |
| `ZENOH_ACTION_TOPIC` | `robot/tunnel/action` | Paid action subscription |
| `ZENOH_RESULT_TOPIC` | `robot/tunnel/result` | Correlated terminal result |
| `ZENOH_METRICS_TOPIC` | `robot/reachy_mini/metrics` | Simulator metrics |

The Tunnel uses the same action/result topic variables. Keep both processes on
the same values when overriding the defaults, and configure
`SKILL_CATALOG_PATH=registry/vendors/pollen-robotics/reachy-mini/pollen-robotics.reachy-mini.mujoco-webots-sim.v1/skill-catalog.json`
with `ALLOWED_ACTIONS=look_at_apple,inspect_table,stop`. Missing either setting
fails closed before Zenoh publication.

The bridge accepts only canonical registered skills with the complete Tunnel
correlation tuple (`action_id`, `robot_id`, `skill_id`, `params_hash`, and
`idempotency_key`). `stop` accepts no parameters and zeros every actuator; an
unknown action, unknown target, or inconsistent tuple never starts object
tracking. Robot WebSocket/payee cryptographic binding remains an upstream shared
Tunnel/Gateway protocol dependency; this profile intentionally does not invent
an EIP handshake.

## Clean-checkout setup and run

Run these commands from the repository root. They use the same profile ID in
the registry, Tunnel, and bridge; change it only as one coordinated deployment
override.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r bridge/reachy_mini/mujoco_sim_bridge/requirements.txt
python -m pip install git+https://github.com/pollen-robotics/reachy_mini.git@v1.8.4

# Build the real Go Tunnel and its matching zenoh-c dependency.
make build

# Start a local Zenoh router in a second terminal (or point both processes at
# an existing router with ZENOH_CONFIG).
wget -q https://download.eclipse.org/zenoh/zenoh/1.9.0/zenoh-1.9.0-x86_64-unknown-linux-gnu-standalone.zip
unzip -q zenoh-1.9.0-x86_64-unknown-linux-gnu-standalone.zip -d .zenoh-router
.zenoh-router/zenohd
```

In two additional terminals, export the same identity/topics and start the
Tunnel and bridge. Replace the payee only in an untracked `.env` or secret
manager; the zero payee in `tunnel/config.json` is intentionally rejected.

```bash
export ROBOT_ID=reachy-mini-kauker
export ROBO_PAYEE_ADDRESS=0xYourBaseSepoliaPayee
export ROBO_NETWORK=eip155:84532
export ROBO_PRICE=0.001
export ZENOH_ENDPOINT=tcp/127.0.0.1:7447
export ZENOH_ACTION_TOPIC=robot/tunnel/action
export ZENOH_RESULT_TOPIC=robot/tunnel/result
export ZENOH_METRICS_TOPIC=robot/reachy_mini/metrics
export SKILL_CATALOG_PATH=registry/vendors/pollen-robotics/reachy-mini/pollen-robotics.reachy-mini.mujoco-webots-sim.v1/skill-catalog.json
export ALLOWED_ACTIONS=look_at_apple,inspect_table,stop

# Terminal A
make run

# Terminal B
python bridge/reachy_mini/mujoco_sim_bridge/main.py
```

Use `python bridge/reachy_mini/test_e2e_paid_action.py` for the reproducible
local paid-flow proof. The paid `POST /action` returns `202` plus an
`action_id`; poll `GET /action/<action_id>/status` for `succeeded`, `failed`,
or `timeout` and the correlated simulator result. Install Webots R2025a before
running the cross-engine Sim-to-Sim validation in CI or locally.

## Tests

```bash
# Local E2E (proxy + facilitator, no real funds)
python3 test_e2e_paid_action.py

# Mandatory no-settlement proof: simulator failure/timeout/replay/restart
# against the real tunnel binary + recording facilitator (zero /settle calls)
python3 test_x402_no_settlement.py

# Live Base Sepolia E2E (needs PRIVATE_KEY env var); writes base_sepolia_result_<ts>.json
python3 test_base_sepolia_tunnel_e2e.py

# Payment gate vs real Go binary
python3 test_payment_gate.py
```

## Requirements

Pinned in `mujoco_sim_bridge/requirements.txt` (same versions as CI):
Python 3.10, `mujoco==3.10.0`, `numpy==2.2.6`, `eclipse-zenoh==1.9.0`,
`x402[requests,evm]==2.16.0`, `eth-account==0.13.7`, `requests==2.32.3`,
`reachy_mini` from git tag `v1.8.4`, Webots R2025a (for sim2sim).
