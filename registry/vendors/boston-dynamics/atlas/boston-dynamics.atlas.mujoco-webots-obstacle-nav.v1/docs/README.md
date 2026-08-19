# Atlas MuJoCo Obstacle Navigation

**Profile:** `boston-dynamics.atlas.mujoco-webots-obstacle-nav.v1`

## Overview

Policy-driven bipedal obstacle avoidance for the MuJoCo humanoid model,
demonstrating Zenoh bridge integration with x402 micropayment settlement.

## Skills

| Skill | Description | Price |
|-------|-------------|-------|
| `navigate_obstacles` | Drive Atlas through obstacle corridor | 0.001 USDC |
| `stop` | Interrupt active episode safely | 0.001 USDC |

## Model

- **Source:** [google-deepmind/mujoco](https://github.com/google-deepmind/mujoco) `model/humanoid/humanoid.xml`
- **Actuators:** 21 general (motor) actuators with asymmetric gear ratios
- **License:** Apache-2.0

### Known Constraints

The MuJoCo humanoid model has **gear=20 ankle actuators** (weakest joint),
which cannot maintain static upright balance. The sinusoidal gait controller
exploits forward momentum for locomotion. This is a model limitation — the
model was designed for RL-trained policies, not classical PD control.

## Architecture

```
Client → x402 → Tunnel → Zenoh → bridge.py → runner.py → MuJoCo
                              ← result ←  metrics ←
```

## Running

```bash
python -m bridge.boston-dynamics.atlas_mujoco_bridge.runner --viewer
```

## Tests

```bash
python -m pytest bridge/boston-dynamics/atlas_mujoco_bridge/tests/ -v
```
