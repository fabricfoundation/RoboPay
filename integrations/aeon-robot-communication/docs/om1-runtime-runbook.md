# OM1 Runtime Runbook

Windows local proof uses `PUBLISHER=stub` and does not verify real Zenoh or ROS2.

Use this runbook on Ubuntu, WSL, or an OM1 machine with Zenoh and ROS2 available.

Do not mark M2 complete until both are observed:

- `zenoh sub -k robot/tunnel/action` receives an action envelope
- `ros2 topic echo /cmd_vel` receives expected Twist output from OM1 bridge

## Prepare Gateway Environment

```bash
cd ~/workspace/aeon-robot-communication
cp .env.example .env
```

Edit `.env`:

```bash
PUBLISHER=zenoh-cli
ZENOH_TOPIC=robot/tunnel/action
AEON_FACILITATOR_URL=http://127.0.0.1:3402
ROBOT_ID=g1-demo-001
```

Install dependencies:

```bash
npm install
```

The gateway requires the `zenoh` CLI on PATH when `PUBLISHER=zenoh-cli`.

## Start Mock AEON

Terminal 1:

```bash
cd ~/workspace/aeon-robot-communication
export MOCK_AEON_PORT=3402
npm run dev:mock-aeon
```

## Start Gateway With Zenoh CLI Publisher

Terminal 2:

```bash
cd ~/workspace/aeon-robot-communication
export PORT=18080
export ROBOT_ID=g1-demo-001
export AEON_FACILITATOR_URL=http://127.0.0.1:3402
export PUBLISHER=zenoh-cli
export ZENOH_TOPIC=robot/tunnel/action
npm run dev
```

Expected startup:

```text
gateway listening on http://127.0.0.1:18080 robotId=g1-demo-001 publisher=zenoh-cli topic=robot/tunnel/action
```

If `zenoh` is not available, paid action publish must fail clearly. It must not silently succeed.

## Observe Zenoh

Terminal 3:

```bash
zenoh sub -k robot/tunnel/action
```

## Send Runtime Verification Action

Terminal 4:

```bash
cd ~/workspace/aeon-robot-communication
export GATEWAY_URL=http://127.0.0.1:18080
export ROBOT_ID=g1-demo-001
npm run verify:runtime
```

Expected:

```text
zenoh sub receives action envelope with skillId=move_forward
```

The Zenoh payload should include:

- `actionId`
- `robotId`
- `skillId`
- `params`
- `idempotencyKey`
- `paramsHash`
- `payment.provider`
- `payment.txHash`
- `payment.payer`
- `payment.payTo`
- `payment.amount`
- `payment.asset`
- `payment.network`
- `issuedAt`
- `expiresAt`

Example envelope fields the OM1 bridge must parse:

```json
{
  "skillId": "move_forward",
  "params": {
    "durationSec": 3,
    "speed": 0.5
  }
}
```

## ROS2 Verification

Terminal 5:

```bash
source /opt/ros/humble/setup.bash
ros2 topic echo /cmd_vel
```

Start the OM1 bridge so it subscribes to:

```text
robot/tunnel/action
```

The bridge should parse the action envelope by:

- reading `skillId`
- reading `params`
- ignoring but preserving `payment` metadata in logs or telemetry
- rejecting unsupported `skillId` with a clear error
- clipping `durationSec`, `speed`, and `angularSpeed` to safe robot limits

If the current OM1 bridge only supports an old payload shape, add compatibility before claiming M2:

- prefer `skillId`
- prefer `params`
- optionally support legacy `action`
- do not require payment verification in the bridge for the same already-authorized action

Expected `/cmd_vel` mappings:

- `move_forward` -> positive `linear.x`
- `turn_left` -> positive `angular.z`
- `turn_right` -> negative `angular.z`
- `stop` -> zero Twist

Concrete checks:

```bash
export SKILL_ID=move_forward
export IDEMPOTENCY_KEY=aeon-om1-move-forward-001
npm run send:paid

export SKILL_ID=turn_left
export IDEMPOTENCY_KEY=aeon-om1-turn-left-001
npm run send:paid

export SKILL_ID=turn_right
export IDEMPOTENCY_KEY=aeon-om1-turn-right-001
npm run send:paid

export SKILL_ID=stop
export IDEMPOTENCY_KEY=aeon-om1-stop-001
npm run send:paid
```

## OM1 Sim

Run OM1-sim/G1 according to the local robot runtime instructions, then repeat the paid action flow and observe visible movement.

Record:

- Zenoh subscriber output
- `/cmd_vel` output
- visible OM1-sim/G1 movement
- any bridge parsing errors
