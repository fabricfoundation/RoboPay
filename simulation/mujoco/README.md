# G1 Obstacle Navigation Simulation

MuJoCo simulation for Unitree G1 obstacle navigation with RoboPay integration.

## Quick Start

```bash
# Install dependencies
pip install mujoco numpy

# Run simulation (headless)
python3 scripts/run_simulation.py --headless

# Run with visualization
python3 scripts/run_simulation.py
```

## Architecture

```
scripts/
  run_simulation.py     # Main simulation runner
  validate_navigation.py # Sim-to-Sim validation
  download_models.sh    # Download MuJoCo Menagerie models
scenes/
  g1_obstacle_nav.xml   # Obstacle navigation scene
models/                 # Downloaded robot models (gitignored)
```

## How It Works

1. MuJoCo loads the G1 humanoid model with obstacles
2. Policy computes velocity commands from simulator state
3. Physics engine simulates robot motion and contacts
4. Metrics are collected from simulator (collision, position, velocity)

## Metrics

All metrics come from the MuJoCo physics engine, not Python code:

| Metric | Source | Description |
|--------|--------|-------------|
| `collision_count` | `mj_contactForce` | Number of contact events |
| `contact_force_magnitude` | Physics engine | Total contact force |
| `robot_position` | Body state | Current (x, y, z) position |
| `robot_velocity` | Body state | Current velocity |
| `path_progress` | Computed | Distance-based progress |

## Sim-to-Sim Validation

```bash
python3 scripts/validate_navigation.py
```

Runs the same policy across multiple scenarios and validates consistency.
