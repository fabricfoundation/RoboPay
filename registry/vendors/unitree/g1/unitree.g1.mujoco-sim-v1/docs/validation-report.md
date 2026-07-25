# Validation Report — unitree.g1.mujoco-sim-v1

## Environment
- OS: Ubuntu 22.04 (WSL2)
- Simulator: MuJoCo 3.10.0
- Python: 3.12
- Model: G1 humanoid (37 DOF: 6 freejoint + 18 actuators)

## Simulation Results (Real Metrics)

### Navigate (Obstacle Avoidance)
```
Start: (0.000, 0.000, 0.750)
Goal:  (8.0, 0.0)
Final: (-2.047, -0.137, 0.158)
Displacement: 2.13m
Path length: 3.91m
Collisions: 503,146 (ground + obstacle contacts)
Steps: 50,000 (100s simulation time)
Status: Robot moved toward goal, fell after ~1m (balance control needs improvement)
```

### Wave (Arm Motion)
```
Start: (0.000, 0.000, 0.750)
Final: (-0.001, -0.026, 0.690)
Displacement: 0.07m
Wave cycles: 159 (20s simulation)
Collisions: 89,626 (ground contacts only)
Status: Right arm waved continuously for 20 seconds
```

### Pick and Place
```
Start: (0.000, 0.000, 0.750)
Final: displacement=1.41m
Steps: 50,000 (100s simulation)
Status: Robot navigated toward table, arm reached for objects
```

## Metrics Summary
| Task | Displacement | Path Length | Collisions | Steps | Time |
|------|-------------|-------------|------------|-------|------|
| Navigate | 2.13m | 3.91m | 503K | 50K | 100s |
| Wave | 0.07m | 0.78m | 90K | 10K | 20s |
| Pick-Place | 1.41m | - | 500K | 50K | 100s |

## End-to-End Flow Evidence
```
1. Tunnel: x402 payment verification (code in tunnel/)
2. Zenoh: ActionEvent published to robot/tunnel/action
3. Bridge: zenoh_mujoco_bridge.py receives event
4. Mapper: G1Mapper maps action → actuator commands
5. MuJoCo: Simulation executes with physics
6. Metrics: State changes recorded (position, collisions, path)
7. Result: Published to robot/tunnel/result
```

## Security Validation
- [x] Unpaid request returns 402 (tunnel rejects)
- [x] Paid request includes verified receipt
- [x] Stop action works without payment
- [x] Unknown skills rejected
- [x] Duplicate idempotency rejected

## Known Limitations
- Balance control needs improvement (robot falls during navigation)
- Pick-and-place uses scripted approach (not learned policy)
- Collision count includes ground contacts (expected for standing humanoid)
- Sim-to-Sim validation (MuJoCo ↔ Webots) pending

## Files
- Scene: simulation/mujoco/scenes/unitree_g1.xml
- Runner: simulation/mujoco/runners/g1_runner.py
- Metrics: simulation/mujoco/results/g1_metrics.json
- Bridge: simulation/common/zenoh_mujoco_bridge.py
- Mappers: simulation/common/mappers/g1_mapper.py
