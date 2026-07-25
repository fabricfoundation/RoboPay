# Unitree G1 MuJoCo Simulation Profile

## Overview
This profile connects the Unitree G1 humanoid robot (in MuJoCo simulation) to
the Fabric RoboPay network. Paid actions are verified through the x402 tunnel
and executed via Zenoh → MuJoCo bridge.

## Architecture
```
Fabric → Tunnel (x402) → Zenoh (robot/action) → Bridge → MuJoCo G1 Simulation
                                                         ↓
                                                    State Metrics
                                                         ↓
                                              Zenoh (robot/tunnel/result) → Fabric
```

## Skills
| Skill | Description | Price |
|-------|-------------|-------|
| move_forward | Locomotion forward | 0.01 USDC |
| navigate_obstacle | RRT* + DWA navigation | 0.05 USDC |
| pick_and_place | Object manipulation | 0.10 USDC |
| stop | Emergency stop | Free |

## Quick Start
```bash
# Install
pip install mujoco zenoh-py

# Start bridge
python -m simulation.common.zenoh_mujoco_bridge \
    --scene simulation/mujoco/scenes/unitree_g1.xml \
    --robot g1

# Run demo (in another terminal)
python demo/run_demo.py --skill move_forward
```

## Environment Variables
- `ROBOT_ID`: Robot identifier (default: g1-demo-001)
- `ZENOH_ENDPOINT`: Zenoh router endpoint (default: tcp/127.0.0.1:7447)
- `ROBO_WALLET_PRIVATE_KEY`: Payee wallet private key (NEVER commit this)
- `FABRIC_RELAY_URL`: Fabric relay URL

## Security
- Private keys loaded from env vars only
- Unverified actions never reach the simulator
- Stop/cancel always works (no payment required)
