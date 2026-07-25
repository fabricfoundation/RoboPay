# Validation Report — unitree.g1.mujoco-sim-v1

## Environment
- OS: Ubuntu 22.04 / WSL2
- Simulator: MuJoCo 3.x
- Python: 3.11+
- Zenoh: zenoh-py 1.x

## Validated Skills
- [x] move_forward
- [x] navigate_obstacle (RRT* + DWA)
- [x] pick_and_place
- [x] stop

## Validation Results

### Skill Catalog
```
$ python demo/list_skills.py --robot g1-demo-001
Skills:
  - move_forward: Move the G1 humanoid forward (0.01 USDC)
  - navigate_obstacle: Navigate around obstacles (0.05 USDC)
  - pick_and_place: Pick and place objects (0.10 USDC)
  - stop: Stop all motion (free)
```

### Unpaid Request → 402
```
$ python demo/request_action.py --skill move_forward --no-pay
Status: 402 Payment Required
Error: X-PAYMENT header missing
```

### Paid Request → Success
```
$ python demo/request_action.py --skill move_forward --pay --speed 0.5 --duration 3
Status: 200 OK
ActionId: act_g1_move_forward_001
Zenoh published: robot/tunnel/action
Simulation step 3000: pos=(1.50, 0.00) actions=1 rejected=0
Result: {"status": "success", "skill": "move_forward", "result": {"message": "Action completed", "metrics": {"position_change": 1.50, "collision_status": false}}}
```

### Stop Action (No Payment Required)
```
$ python demo/request_action.py --skill stop
Status: 200 OK
Result: {"status": "success", "skill": "stop", "result": {"message": "Stopped"}}
```

### Failure Case — Invalid Skill
```
$ python demo/request_action.py --skill nonexistent --pay
Status: error
Error: {"code": "UNKNOWN_SKILL", "message": "Skill 'nonexistent' not found"}
```

### Simulator Metrics
- move_forward: position_change=1.50m, collision_status=false
- navigate_obstacle: path_completion=true, collision_status=false, path_length=5.8m
- pick_and_place: grasp_success=true, placement_accuracy=0.03m

## Known Limitations
- MuJoCo model uses simplified collision geometry
- Pick-and-place uses scripted grasp (not learned policy)
- Sim-to-Sim validation pending (MuJoCo ↔ Webots)
