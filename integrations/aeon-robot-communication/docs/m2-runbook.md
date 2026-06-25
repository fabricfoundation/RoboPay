# M2 Runbook

This runbook captures the copy-paste commands used for AEON M2 bridge-stage validation.

## Assumptions

- Ubuntu 22.04
- Node v22.22.1 and npm 10.9.4
- ROS2 Humble
- `zenohd v1.7.2`
- `~/.local/bin/zenoh` is available and supports:
  - `zenoh pub -k robot/tunnel/action -v '<json>'`
  - `zenoh sub -k robot/tunnel/action`
- AEON gateway repo: `~/workspace/Aeon-robot-communication`
- OM1 bridge test path: `~/workspace/fabric_om1/fabric_om1sim_g1_bridge`

## Baseline

```bash
cd ~/workspace/Aeon-robot-communication && npm install && npm run typecheck && npm test
```

## Environment

```bash
cd ~/workspace/Aeon-robot-communication && cp .env.example .env
```

Edit `.env` so these values are set:

```dotenv
PORT=18080
ROBOT_ID=g1-demo-001
X402_PROVIDER=aeon-bnb-x402
AEON_FACILITATOR_URL=http://127.0.0.1:3402
NETWORK=eip155:56
ASSET=USDT_OR_USDC_CONTRACT
PAY_TO=0x0000000000000000000000000000000000000001
AMOUNT=10000
ZENOH_TOPIC=robot/tunnel/action
PUBLISHER=zenoh-cli
```

`ASSET=USDT_OR_USDC_CONTRACT` is a placeholder and must be replaced before real-chain testing.

## Zenoh Router

```bash
zenohd
```

Expected router endpoint:

```text
tcp/127.0.0.1:7447
```

## Mock AEON Facilitator

```bash
cd ~/workspace/Aeon-robot-communication && MOCK_AEON_PORT=3402 npm run dev:mock-aeon
```

## Gateway With Real Zenoh Publisher

```bash
cd ~/workspace/Aeon-robot-communication && export PATH="$HOME/.local/bin:$PATH" && PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action npm run dev
```

Expected gateway log:

```text
publisher=zenoh-cli
topic=robot/tunnel/action
```

## Zenoh Subscriber

```bash
export PATH="$HOME/.local/bin:$PATH" && zenoh sub -k robot/tunnel/action
```

## Runtime Proof

```bash
cd ~/workspace/Aeon-robot-communication && npm run verify:runtime
```

Expected runtime statuses:

```text
skills status 200
unpaid status 402
paid status 200
duplicate status 200, published=false
modifiedParams status 409
wrongRobot status 404
wrongSkill status 404
```

Expected Zenoh subscriber payload:

```text
robotId=g1-demo-001
skillId=move_forward
payment.provider=aeon-bnb-x402
payment.txHash=0xmocktx
authorization.type=local-hmac-sha256
```

## Four-Action Zenoh Publish Proof

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=move_forward IDEMPOTENCY_KEY=aeon-move-001 npm run send:paid
```

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=turn_left IDEMPOTENCY_KEY=aeon-left-001 npm run send:paid
```

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=turn_right IDEMPOTENCY_KEY=aeon-right-001 npm run send:paid
```

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=stop IDEMPOTENCY_KEY=aeon-stop-001 npm run send:paid
```

Expected Zenoh subscriber output:

```text
skillId=move_forward, idempotencyKey=aeon-move-001
skillId=turn_left, idempotencyKey=aeon-left-001
skillId=turn_right, idempotencyKey=aeon-right-001
skillId=stop, idempotencyKey=aeon-stop-001
```

## OM1 Bridge To ROS2 /cmd_vel

Start ROS2 echo:

```bash
source /opt/ros/humble/setup.bash && ros2 topic echo /cmd_vel geometry_msgs/msg/Twist
```

Start bridge:

```bash
cd ~/workspace/fabric_om1/fabric_om1sim_g1_bridge && source /opt/ros/humble/setup.bash && python3 src/fabric_to_om1_adapter.py --zenoh-topic robot/tunnel/action --cmd-vel-topic /cmd_vel --zenoh-endpoint tcp/127.0.0.1:7447
```

Send move-forward action:

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=move_forward IDEMPOTENCY_KEY=aeon-move-ros-001 npm run send:paid
```

Expected bridge log:

```text
received zenoh sample
mapped skillId=move_forward to Twist linear.x=0.5 angular.z=0.0 duration=3.0
published final zero Twist
```

Expected `/cmd_vel`:

```yaml
linear:
  x: 0.5
  y: 0.0
  z: 0.0
angular:
  x: 0.0
  y: 0.0
  z: 0.0
```

## Milestone Language

Use this wording for the current stage:

```text
The AEON gateway integration has completed the M2 bridge-stage validation.
The new AEON payment/action gateway was verified up to ROS2 /cmd_vel.
The downstream OM1-sim/G1 response to /cmd_vel was previously validated in the Fabric-OM1 integration path and was not repeated in this AEON run.
```
