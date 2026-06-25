# M2 AEON Zenoh OM1 Validation Report

## Environment

- OS: Ubuntu 22.04
- Node: v22.22.1
- npm: 10.9.4
- ROS2: Humble
- Zenoh router: `zenohd v1.7.2`
- AEON gateway repo: `~/workspace/Aeon-robot-communication`
- OM1 bridge test path: `~/workspace/fabric_om1/fabric_om1sim_g1_bridge`
- Zenoh topic: `robot/tunnel/action`
- ROS2 output topic: `/cmd_vel`
- Robot ID: `g1-demo-001`
- Publisher mode for M2-A: `zenoh-cli`

## Configuration

Runtime `.env` used for the AEON gateway:

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

`ASSET=USDT_OR_USDC_CONTRACT` is still a placeholder. It must be replaced with a real USDT or USDC contract address before real-chain testing.

## Commands Used

Baseline:

```bash
cd ~/workspace/Aeon-robot-communication && npm install
cd ~/workspace/Aeon-robot-communication && npm run typecheck
cd ~/workspace/Aeon-robot-communication && npm test
cd ~/workspace/Aeon-robot-communication && npm run verify:runtime
```

Mock AEON facilitator:

```bash
cd ~/workspace/Aeon-robot-communication && MOCK_AEON_PORT=3402 npm run dev:mock-aeon
```

Gateway with real Zenoh publisher mode:

```bash
cd ~/workspace/Aeon-robot-communication && export PATH="$HOME/.local/bin:$PATH" && PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action npm run dev
```

Zenoh subscriber:

```bash
export PATH="$HOME/.local/bin:$PATH" && zenoh sub -k robot/tunnel/action
```

Runtime verification:

```bash
cd ~/workspace/Aeon-robot-communication && npm run verify:runtime
```

Four-action publish verification:

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=move_forward IDEMPOTENCY_KEY=aeon-move-001 npm run send:paid
cd ~/workspace/Aeon-robot-communication && SKILL_ID=turn_left IDEMPOTENCY_KEY=aeon-left-001 npm run send:paid
cd ~/workspace/Aeon-robot-communication && SKILL_ID=turn_right IDEMPOTENCY_KEY=aeon-right-001 npm run send:paid
cd ~/workspace/Aeon-robot-communication && SKILL_ID=stop IDEMPOTENCY_KEY=aeon-stop-001 npm run send:paid
```

OM1 bridge proof:

```bash
cd ~/workspace/fabric_om1/fabric_om1sim_g1_bridge && source /opt/ros/humble/setup.bash && python3 src/fabric_to_om1_adapter.py --zenoh-topic robot/tunnel/action --cmd-vel-topic /cmd_vel --zenoh-endpoint tcp/127.0.0.1:7447
```

ROS2 `/cmd_vel` echo:

```bash
source /opt/ros/humble/setup.bash && ros2 topic echo /cmd_vel geometry_msgs/msg/Twist
```

## Runtime Proof Results

Ubuntu baseline/runtime proof passed:

- `npm run typecheck`: passed
- `npm test`: passed
- test files: `5`
- tests: `16/16`
- `GET /v1/robots/g1-demo-001/skills`: `200`
- unpaid `POST /v1/robots/g1-demo-001/actions`: `402 PAYMENT_REQUIRED`
- paid action: `200 accepted`
- duplicate idempotency request: `200`, `published=false`
- same idempotency key with modified params: `409 IDEMPOTENCY_CONFLICT`
- wrong robot: `404 ROBOT_NOT_FOUND`
- wrong skill: `404 SKILL_NOT_FOUND`

Key runtime semantics were preserved:

- unpaid action correctly returns `PAYMENT_REQUIRED / 402`
- paid action returns an accepted action and a payment receipt
- duplicate idempotency avoids repeated publish
- modified params under the same idempotency key are rejected
- wrong robot and wrong skill are rejected

## Real Zenoh Publish Proof

The Ubuntu machine initially did not have a compatible `zenoh` CLI. The Eclipse Zenoh Python client was installed, and a local compatibility wrapper was added:

```text
~/.local/bin/zenoh
```

The wrapper supports the AEON gateway's current CLI usage:

```bash
zenoh pub -k robot/tunnel/action -v '<json>'
zenoh sub -k robot/tunnel/action
```

The official Zenoh router was started:

```bash
zenohd
```

Router endpoint:

```text
tcp/127.0.0.1:7447
```

The older `fabric_to_om1_adapter` process that previously occupied `7447` was stopped so `zenohd` could act as the unified router.

Local Zenoh pub/sub sanity check passed:

```bash
export PATH="$HOME/.local/bin:$PATH" && zenoh sub -k robot/tunnel/action
export PATH="$HOME/.local/bin:$PATH" && zenoh pub -k robot/tunnel/action -v '{"test":true}'
```

Subscriber observed:

```text
robot/tunnel/action: {"test":true}
```

AEON gateway real publish proof passed with:

```bash
cd ~/workspace/Aeon-robot-communication && export PATH="$HOME/.local/bin:$PATH" && PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action npm run dev
cd ~/workspace/Aeon-robot-communication && npm run verify:runtime
```

The Zenoh subscriber received an action envelope containing:

- `robotId=g1-demo-001`
- `skillId=move_forward`
- `payment.provider=aeon-bnb-x402`
- `payment.txHash=0xmocktx`
- `authorization.type=local-hmac-sha256`

Conclusion:

```text
M2-A real Zenoh publish proof passed.
```

## Four-Action Zenoh Publish Proof

With `zenohd`, mock AEON facilitator, gateway `PUBLISHER=zenoh-cli`, and `zenoh sub -k robot/tunnel/action` running, these commands were executed:

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=move_forward IDEMPOTENCY_KEY=aeon-move-001 npm run send:paid
cd ~/workspace/Aeon-robot-communication && SKILL_ID=turn_left IDEMPOTENCY_KEY=aeon-left-001 npm run send:paid
cd ~/workspace/Aeon-robot-communication && SKILL_ID=turn_right IDEMPOTENCY_KEY=aeon-right-001 npm run send:paid
cd ~/workspace/Aeon-robot-communication && SKILL_ID=stop IDEMPOTENCY_KEY=aeon-stop-001 npm run send:paid
```

The Zenoh subscriber received four corresponding envelopes:

- `skillId=move_forward`, `idempotencyKey=aeon-move-001`
- `skillId=turn_left`, `idempotencyKey=aeon-left-001`
- `skillId=turn_right`, `idempotencyKey=aeon-right-001`
- `skillId=stop`, `idempotencyKey=aeon-stop-001`

Each envelope included:

- payment receipt metadata
- `payment.provider=aeon-bnb-x402`
- `payment.txHash=0xmocktx`
- local HMAC authorization metadata

Conclusion:

```text
M2-A four-action Zenoh publish proof passed.
```

## ROS2 /cmd_vel Bridge Proof

The local Fabric-OM1 bridge directory contained:

```text
configs/
logs/
src/
src/__pycache__/fabric_to_om1_adapter.cpython-310.pyc
tests/
```

The source entrypoint `src/fabric_to_om1_adapter.py` was not present, and only a `.pyc` cache remained. To validate the current AEON bridge stage, a lightweight bridge was temporarily reconstructed at:

```text
~/workspace/fabric_om1/fabric_om1sim_g1_bridge/src/fabric_to_om1_adapter.py
```

The lightweight bridge:

- connects to Zenoh at `tcp/127.0.0.1:7447`
- subscribes to `robot/tunnel/action`
- parses the AEON action envelope
- maps `skillId` and `params` to `geometry_msgs/msg/Twist`
- publishes to ROS2 `/cmd_vel`

Bridge startup command:

```bash
cd ~/workspace/fabric_om1/fabric_om1sim_g1_bridge && source /opt/ros/humble/setup.bash && python3 src/fabric_to_om1_adapter.py --zenoh-topic robot/tunnel/action --cmd-vel-topic /cmd_vel --zenoh-endpoint tcp/127.0.0.1:7447
```

Bridge startup logs confirmed:

```text
subscribed zenoh topic: robot/tunnel/action
publishing ROS2 Twist topic: /cmd_vel
zenoh endpoint: tcp/127.0.0.1:7447
```

Move-forward action:

```bash
cd ~/workspace/Aeon-robot-communication && SKILL_ID=move_forward IDEMPOTENCY_KEY=aeon-move-ros-001 npm run send:paid
```

Bridge observed:

```text
received zenoh sample: {... "skillId":"move_forward", "params":{"durationSec":3,"speed":0.5}, ...}
mapped skillId=move_forward to Twist linear.x=0.5 angular.z=0.0 duration=3.0
published final zero Twist
```

ROS2 echo command:

```bash
source /opt/ros/humble/setup.bash && ros2 topic echo /cmd_vel geometry_msgs/msg/Twist
```

Observed `/cmd_vel` output:

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

Conclusion:

```text
M2-B Zenoh -> OM1 bridge -> ROS2 /cmd_vel proof passed for move_forward.
```

## Known Caveats

- The lightweight OM1 bridge was used to validate the ROS2 `/cmd_vel` interface because the previous bridge source file was not present in the local Fabric-OM1 directory, only a `.pyc` cache remained.
- The OM1-sim/G1 visible motion proof is not repeated in this AEON run; it is inherited from prior Fabric-OM1 validation.
- `ASSET` remains a placeholder and must be replaced with a real USDT or USDC contract address before real-chain testing.
- The current AEON facilitator is mock local AEON at `http://127.0.0.1:3402`, with mock `txHash=0xmocktx`.
- The local `~/.local/bin/zenoh` wrapper was used to provide CLI compatibility with the gateway's current `zenoh pub -k ... -v ...` invocation.
- The bridge-stage proof confirms `/cmd_vel` output for `move_forward`; the four-action Zenoh envelope publish was validated, but ROS2 `/cmd_vel` echo was specifically recorded for `move_forward`.

## Final Milestone Conclusion

The AEON gateway integration has completed the M2 bridge-stage validation.

The new AEON payment/action gateway was verified up to ROS2 `/cmd_vel`.

The downstream OM1-sim/G1 response to `/cmd_vel` was previously validated in the Fabric-OM1 integration path and was not repeated in this AEON run.

Validated chain:

```text
mock AEON paid action
-> AEON robot action gateway authorization
-> real Zenoh publish to robot/tunnel/action
-> OM1 bridge receives action envelope
-> ROS2 /cmd_vel publishes expected Twist for move_forward
```

Remaining before real AEON staging:

- replace mock facilitator with AEON staging facilitator
- replace placeholder `ASSET` with a real token contract address
- confirm API key and auth header format
- confirm network and amount unit
- confirm `/verify` and `/settle` schemas
- confirm production deployment mode for AEON AIGateway and first-party robot service routing
