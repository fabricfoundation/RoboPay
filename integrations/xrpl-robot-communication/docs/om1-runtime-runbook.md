# OM1 Runtime Runbook

Use this after M1 and M1.5 have passed.

## Requirements

- Ubuntu or OM1 machine
- Node.js
- Zenoh router and CLI
- ROS2 Humble
- OM1 bridge subscribing to `robot/tunnel/action`

## Commands

```bash
zenohd
```

```bash
zenoh sub -k robot/tunnel/action
```

```bash
source /opt/ros/humble/setup.bash && ros2 topic echo /cmd_vel geometry_msgs/msg/Twist
```

```bash
cd ~/workspace/XRPL-robot-communication && MOCK_XRPL_PORT=3402 npm run dev:mock-xrpl
```

```bash
cd ~/workspace/XRPL-robot-communication && PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action XRPL_FACILITATOR_URL=http://127.0.0.1:3402 npm run dev
```

```bash
cd ~/workspace/XRPL-robot-communication && SKILL_ID=move_forward IDEMPOTENCY_KEY=xrpl-move-001 npm run send:paid
cd ~/workspace/XRPL-robot-communication && SKILL_ID=turn_left IDEMPOTENCY_KEY=xrpl-left-001 npm run send:paid
cd ~/workspace/XRPL-robot-communication && SKILL_ID=turn_right IDEMPOTENCY_KEY=xrpl-right-001 npm run send:paid
cd ~/workspace/XRPL-robot-communication && SKILL_ID=stop IDEMPOTENCY_KEY=xrpl-stop-001 npm run send:paid
```

## Expected ROS2 Output

- `move_forward`: `linear.x > 0`
- `turn_left`: `angular.z > 0`
- `turn_right`: `angular.z < 0`
- `stop`: zero Twist

