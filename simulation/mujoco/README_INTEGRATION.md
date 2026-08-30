# MuJoCo Sim-to-Sim with RoboPay Integration (Tier 1)

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐     ┌──────────────┐
│ Fabric      │────▶│ Tunnel       │────▶│ Zenoh            │────▶│ MuJoCo       │
│ Backend     │     │ (x402 verify)│     │ (robot/action)   │     │ Bridge       │
└─────────────┘     └──────────────┘     └──────────────────┘     └──────────────┘
                                                                            │
                                                                            ▼
                                                                     ┌──────────────┐
                                                                     │ MuJoCo       │
                                                                     │ Simulation   │
                                                                     └──────────────┘
```

### End-to-End Flow

1. **Payment**: User sends paid action request to Fabric backend
2. **Verification**: Tunnel verifies x402 payment (rejects unverified)
3. **Publication**: Verified ActionEvent published to Zenoh topic `robot/action`
4. **Bridge**: `zenoh_mujoco_bridge.py` subscribes to `robot/action`
5. **Control**: ActionEvent → policy goal → MuJoCo actuators
6. **State**: Simulation state reported back via logs

### Security Model

- **Only tunnel-verified actions reach the simulator**: The Zenoh topic
  `robot/action` is only published by the tunnel after successful x402
  verification. The MuJoCo bridge does NOT accept direct commands.
- **Stop/cancel works**: The `stop` and `cancel` actions zero all velocities.
- **Invalid actions rejected**: Malformed ActionEvents are logged and rejected.

## Quick Start

```bash
# 1. Install dependencies
pip install mujoco zenoh-py

# 2. Start the tunnel (in another terminal)
cd tunnel && go run cmd/main.go

# 3. Start the MuJoCo bridge
python -m simulation.common.zenoh_mujoco_bridge \
    --scene simulation/mujoco/scenes/unitree_g1.xml \
    --robot unitree_g1 \
    --zenoh-endpoint tcp/127.0.0.1:7447

# 4. Send a paid action (via Fabric API)
# The tunnel verifies payment and publishes to Zenoh
# The bridge receives it and controls the simulation
```

## Reproducible Demo

```bash
# Terminal 1: Start tunnel
cd tunnel && go run cmd/main.go

# Terminal 2: Start MuJoCo bridge
python -m simulation.common.zenoh_mujoco_bridge \
    --scene simulation/mujoco/scenes/unitree_g1.xml

# Terminal 3: Send test action (simulates verified payment)
python -c "
import zenoh, json
conf = zenoh.Config.from_json5('{"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}')
session = zenoh.open(conf)
action = {'payload': {'action': 'move_forward', 'params': {'speed': 0.5}}, 'timestamp': '2026-01-01T00:00:00Z'}
session.put('robot/action', json.dumps(action))
print('Sent move_forward action')
session.close()
"

# Expected output in Terminal 2:
# Received action: move_forward (params: {'speed': 0.5}, ts: 2026-01-01T00:00:00Z)
# ACTION: move_forward → goal={'vx': 0.5, 'vy': 0.0, 'wz': 0.0}
# Step 1000: pos=(0.50, 0.00) actions=1 rejected=0
```

## Supported Actions

| Action | Description | Parameters |
|--------|-------------|------------|
| `move_forward` | Move forward at 0.5 m/s | `speed` (optional) |
| `move_backward` | Move backward at 0.3 m/s | - |
| `turn_left` | Turn left at 0.5 rad/s | - |
| `turn_right` | Turn right at 0.5 rad/s | - |
| `navigate` | Navigate to goal coordinates | `goal_x`, `goal_y` |
| `stop` | Stop all motion | - |
| `cancel` | Cancel current action | - |

## Repository Placement

Per the RoboPay architecture, simulator scenes and policies live in
[OM1-sim](https://github.com/OpenMind/OM1-sim). This PR adds only the
**bridge adapter** (`simulation/common/zenoh_mujoco_bridge.py`) that
connects the RoboPay tunnel to the MuJoCo simulator. The MuJoCo model
files and scenes should be moved to OM1-sim before merging.
