# M2 Runbook

M2-A is complete when a real Zenoh subscriber receives the action envelope. Full M2 is not complete until OM1/ROS2 observes `/cmd_vel`.

## M2-A XRPL Testnet to Zenoh

M2-A requires a real XRPL testnet payment and a real Zenoh subscriber.

Windows without a usable `zenoh` command can only verify:

```text
XRPL testnet payment -> PaymentReceipt -> ActionEnvelope -> StubPublisher
```

It cannot claim real Zenoh publish.

The current Windows validation used a local Python wrapper around the Eclipse Zenoh Python client because the official Windows standalone bundle provided `zenohd.exe` but not a `zenoh.exe` CLI. This is still a real Zenoh publish/subscriber proof because the gateway published through a Zenoh client to a running `zenohd` router and `zenoh sub -k robot/tunnel/action` received the envelope.

Terminal 1:

```bash
zenohd
```

Terminal 2:

```bash
zenoh sub -k robot/tunnel/action
```

Terminal 3:

```bash
cd ~/workspace/XRPL-robot-communication && XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai XRPL_NETWORK=xrpl:1 XRPL_ASSET=XRP XRPL_AMOUNT=1000 XRPL_PAY_TO=<XRPL testnet receive address> PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action npm run dev:xrpl-testnet-actions
```

Windows wrapper variant:

```powershell
cd C:\workspace\XRPL-robot-communication && $env:PYTHONPATH="C:\workspace\XRPL-robot-communication\.tools\python"; $env:ZENOH_PYTHON="<python.exe>"; $env:ZENOH_PYTHON_WRAPPER="C:\workspace\XRPL-robot-communication\.tools\bin\zenoh_cli.py"; $env:XRPL_FACILITATOR_URL="https://xrpl-facilitator-testnet.t54.ai"; $env:XRPL_NETWORK="xrpl:1"; $env:XRPL_ASSET="XRP"; $env:XRPL_AMOUNT="1000"; $env:XRPL_PAY_TO="<XRPL testnet receive address>"; $env:PUBLISHER="zenoh-cli"; $env:ZENOH_TOPIC="robot/tunnel/action"; npm run dev:xrpl-testnet-actions
```

Terminal 4:

```bash
cd ~/workspace/XRPL-robot-communication && XRPL_BUYER_SEED=<XRPL testnet payer seed> npm run send:xrpl-testnet-action
```

Expected:

```text
paid action returns status=200
paymentReceipt.provider=xrpl-x402
paymentReceipt.txHash=<XRPL testnet transaction hash>
actionEnvelope.payment.provider=xrpl-x402
zenoh sub receives an ActionEnvelope with payment.provider=xrpl-x402 and skillId=move_forward
```

## Current Local Result

Windows local validation has passed:

```text
XRPL testnet payment -> PaymentReceipt -> signed ActionEnvelope -> real Zenoh publish -> zenoh sub received robot/tunnel/action
```

Observed transaction evidence:

```text
x402 purchase status=success
paymentReceipt.provider=xrpl-x402
paymentReceipt.txHash=D502FDA4...08573
actionEnvelope.skillId=move_forward
duplicate idempotency published=false
zenoh sub received the action envelope
```

M2-B is still pending for this XRPL repository run because the OM1 bridge and ROS2 `/cmd_vel` path were not executed in this validation.

## M2-B OM1 Bridge to ROS2

Current local preflight is recorded in:

```text
docs/m2-b-om1-ros2-preflight-report.md
```

This repository run did not execute M2-B because the current Windows/WSL environment does not have ROS2 Humble or the OM1 bridge runtime installed.

```bash
source /opt/ros/humble/setup.bash && ros2 topic echo /cmd_vel geometry_msgs/msg/Twist
```

The OM1 bridge should subscribe to:

```text
robot/tunnel/action
```

Expected mappings:

```text
move_forward -> linear.x > 0
turn_left -> angular.z > 0
turn_right -> angular.z < 0
stop -> zero velocity
```
