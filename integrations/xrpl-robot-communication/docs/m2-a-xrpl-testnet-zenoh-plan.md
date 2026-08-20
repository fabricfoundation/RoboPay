# M2-A XRPL Testnet to Zenoh Validation

Status: complete for XRPL testnet payment to real Zenoh publish.

## Scope

M2-A proves:

```text
XRPL testnet x402 payment success
-> PaymentReceipt
-> signed ActionEnvelope
-> publisher emits robot/tunnel/action
```

M2-A completion requires `PUBLISHER=zenoh-cli` and a real `zenoh sub -k robot/tunnel/action` subscriber receiving the envelope.

## Windows Zenoh Note

If the official `zenoh` CLI is not installed on Windows, use either:

- a real `zenoh` executable available in `PATH`;
- `ZENOH_CLI_PATH=<path-to-zenoh>`;
- `ZENOH_PYTHON_WRAPPER=<repo>/.tools/bin/zenoh_cli.py` with `ZENOH_PYTHON` and `PYTHONPATH` set for the local Python Zenoh wrapper.

Without one of these, the machine can only prove:

```text
XRPL testnet x402 payment success
-> PaymentReceipt
-> signed ActionEnvelope
-> StubPublisher accepted payload
```

Do not report this as real Zenoh verified.

## Local Stub Validation Result

Runtime:

```text
OS=Windows
publisher=stub
facilitatorUrl=https://xrpl-facilitator-testnet.t54.ai
network=xrpl:1
asset=XRP
amount=1000 drops
```

Observed:

```text
skills status=200
unpaid action status=402
PAYMENT-REQUIRED header present=true
paid action status=200
x402 purchase status=success
PAYMENT-RESPONSE success=true
PAYMENT-RESPONSE network=xrpl:1
PAYMENT-RESPONSE transaction=EE865C45...DB0CE34
paymentReceipt.provider=xrpl-x402
paymentReceipt.txHash=EE865C45...DB0CE34
actionEnvelope.payment.provider=xrpl-x402
actionEnvelope.skillId=move_forward
duplicate status=200, published=false
modifiedParams status=409
wrongRobot status=404
wrongSkill status=404
```

Security note:

```text
The XRPL testnet payer seed was supplied only through local environment variables.
No wallet seed is committed or recorded in this report.
```

Conclusion:

```text
Early stub-only validation proved XRPL testnet payment -> PaymentReceipt -> signed ActionEnvelope.
That stub-only run did not count as real Zenoh verification.
```

## Real Zenoh Validation Result

Runtime:

```text
OS=Windows
publisher=zenoh-cli
zenohd=1.10.0 local router
subscriber=local Python Zenoh wrapper subscribed to robot/tunnel/action
facilitatorUrl=https://xrpl-facilitator-testnet.t54.ai
network=xrpl:1
asset=XRP
amount=1000 drops
```

Observed:

```text
skills status=200
unpaid action status=402
PAYMENT-REQUIRED header present=true
paid action status=200
x402 purchase status=success
PAYMENT-RESPONSE success=true
PAYMENT-RESPONSE network=xrpl:1
PAYMENT-RESPONSE transaction=D502FDA4...08573
paymentReceipt.provider=xrpl-x402
paymentReceipt.txHash=D502FDA4...08573
actionEnvelope.payment.provider=xrpl-x402
actionEnvelope.skillId=move_forward
duplicate status=200, published=false
modifiedParams status=409
wrongRobot status=404
wrongSkill status=404
zenoh sub received action envelope on robot/tunnel/action
```

Zenoh payload evidence:

```text
topic=robot/tunnel/action
actionId=act_a6f009d2-f1f4-4bf5-a41a-442ec121be5c
robotId=g1-demo-001
skillId=move_forward
idempotencyKey=xrpl-m2a-zenoh-008
payment.provider=xrpl-x402
payment.network=xrpl:1
payment.asset=XRP
payment.txHash=D502FDA4...08573
payment.amount=1000
authorization.type=local-hmac-sha256
```

Conclusion:

```text
M2-A is complete: XRPL testnet payment success produced a signed ActionEnvelope, and a real Zenoh subscriber received it on robot/tunnel/action.
M2-B is not complete in this repository run because OM1 bridge and ROS2 /cmd_vel were not executed here.
```

## Commands

Gateway:

```bash
XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai XRPL_NETWORK=xrpl:1 XRPL_ASSET=XRP XRPL_AMOUNT=1000 XRPL_PAY_TO=<XRPL testnet receive address> PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action npm run dev:xrpl-testnet-actions
```

Subscriber:

```bash
zenoh sub -k robot/tunnel/action
```

Client:

```bash
XRPL_BUYER_SEED=<XRPL testnet payer seed> npm run send:xrpl-testnet-action
```

Windows local wrapper example:

```powershell
$env:PYTHONPATH="C:\workspace\XRPL-robot-communication\.tools\python"
$env:ZENOH_PYTHON="C:\Users\<user>\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:ZENOH_PYTHON_WRAPPER="C:\workspace\XRPL-robot-communication\.tools\bin\zenoh_cli.py"
$env:PUBLISHER="zenoh-cli"
$env:ZENOH_TOPIC="robot/tunnel/action"
npm run dev:xrpl-testnet-actions
```

## Required Evidence

- unpaid action returns `402`
- paid action returns `200`
- `PAYMENT-RESPONSE` has `success=true`
- `PaymentReceipt.provider=xrpl-x402`
- `PaymentReceipt.txHash` is an XRPL testnet transaction
- `ActionEnvelope.payment.provider=xrpl-x402`
- duplicate idempotency returns `published=false`
- modified params return `409`
- wrong robot returns `404`
- wrong skill returns `404`
- `zenoh sub` receives the action envelope
