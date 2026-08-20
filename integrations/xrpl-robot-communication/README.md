# XRPL Robot Communication

Independent XRPL x402 robot action gateway for proving payment-gated robot task execution.

This repository is self-contained. It does not depend on `Aeon-robot-communication`, `robopay`, `robot-tunnel-client`, or any other local repository.

## Source And History

This directory was imported from:

```text
https://github.com/fabricfoundation/XRPL-robot-communication
```

Source commit:

```text
dc8f49561303ae5c4c26d52484bfcd439f61ee24
```

Because this project is imported as a subdirectory inside `robot-tunnel-client`, Git records the import here as new files in this repository. The original per-commit development history remains in the source repository above.

## What This Proves

M1 mock proof:

```text
Agent / payer
-> robot first-party service API
-> XRPL x402 PAYMENT-REQUIRED / PAYMENT-SIGNATURE
-> mock XRPL facilitator verify/settle
-> payment receipt and action authorization
-> StubPublisher capture for robot/tunnel/action
```

The safety property is as important as the happy path:

```text
missing, failed, expired, mismatched, or replayed payment
-> no PaymentReceipt
-> no ActionEnvelope
-> no Zenoh publish
-> no robot action
```

## Setup

```powershell
cd C:\workspace\XRPL-robot-communication
npm install
npm run typecheck
npm test
```

## Local M1 Runtime

Terminal 1:

```powershell
cd C:\workspace\XRPL-robot-communication
$env:MOCK_XRPL_PORT="3402"
npm run dev:mock-xrpl
```

Terminal 2:

```powershell
cd C:\workspace\XRPL-robot-communication
$env:PORT="18080"
$env:ROBOT_ID="g1-demo-001"
$env:XRPL_FACILITATOR_URL="http://127.0.0.1:3402"
$env:PUBLISHER="stub"
npm run dev
```

Terminal 3:

```powershell
cd C:\workspace\XRPL-robot-communication
$env:GATEWAY_URL="http://127.0.0.1:18080"
npm run verify:runtime
```

## XRPL Testnet Payment-Only Probe

The M1.5 probe verifies the real XRPL x402 testnet payment flow only. It does not publish Zenoh and does not execute robot actions.

Use XRPL testnet first:

```env
XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai
XRPL_NETWORK=xrpl:1
XRPL_ASSET=XRP
XRPL_AMOUNT=1000
XRPL_PAY_TO=<XRPL testnet receive address>
XRPL_BUYER_SEED=<XRPL testnet payer seed>
```

Then run:

```powershell
npm run dev:xrpl-testnet-probe
npm run send:xrpl-testnet
```

Never commit `XRPL_BUYER_SEED` or any production wallet secret.

## XRPL Testnet Paid Action

The M2-A action gateway uses real XRPL testnet x402 settlement before it creates a robot action envelope.

Local Windows proof can use `PUBLISHER=stub`:

```powershell
$env:XRPL_FACILITATOR_URL="https://xrpl-facilitator-testnet.t54.ai"
$env:XRPL_NETWORK="xrpl:1"
$env:XRPL_ASSET="XRP"
$env:XRPL_AMOUNT="1000"
$env:XRPL_PAY_TO="<XRPL testnet receive address>"
$env:PUBLISHER="stub"
npm run dev:xrpl-testnet-actions
```

In another terminal:

```powershell
$env:XRPL_BUYER_SEED="<XRPL testnet payer seed>"
npm run send:xrpl-testnet-action
```

Ubuntu/OM1 proof should use `PUBLISHER=zenoh-cli` and a real subscriber:

```bash
zenoh sub -k robot/tunnel/action
PUBLISHER=zenoh-cli ZENOH_TOPIC=robot/tunnel/action npm run dev:xrpl-testnet-actions
npm run send:xrpl-testnet-action
```

Current local result:

```text
XRPL testnet payment -> PaymentReceipt -> signed ActionEnvelope -> real Zenoh publish has passed.
The subscriber received the ActionEnvelope on robot/tunnel/action.
M2-B OM1 bridge / ROS2 /cmd_vel is still pending for this XRPL repository run.
```

On Windows, if the official `zenoh` CLI is unavailable, the publisher can be pointed at a local Python Zenoh wrapper:

```powershell
$env:PYTHONPATH="C:\workspace\XRPL-robot-communication\.tools\python"
$env:ZENOH_PYTHON="<python.exe>"
$env:ZENOH_PYTHON_WRAPPER="C:\workspace\XRPL-robot-communication\.tools\bin\zenoh_cli.py"
$env:PUBLISHER="zenoh-cli"
```

Do not use `PUBLISHER=stub` as evidence for real Zenoh delivery.

## Supported Robot Skills

- `move_forward`: `{ "durationSec": number, "speed": number }`
- `turn_left`: `{ "durationSec": number, "angularSpeed": number }`
- `turn_right`: `{ "durationSec": number, "angularSpeed": number }`
- `stop`: `{}`

`wave` is intentionally unsupported.

## Docs

- [Architecture](docs/architecture.md)
- [API Contract](docs/api-contract.md)
- [XRPL x402 Notes](docs/xrpl-x402-notes.md)
- [XRPL Testnet Runbook](docs/xrpl-testnet-runbook.md)
- [M1 Verification Report](docs/m1-verification-report.md)
- [M1.5 XRPL Testnet Payment-Only Plan](docs/m1-5-xrpl-testnet-payment-only-plan.md)
- [M2 Runbook](docs/m2-runbook.md)
- [M2-B OM1 / ROS2 Preflight Report](docs/m2-b-om1-ros2-preflight-report.md)
- [OM1 Runtime Runbook](docs/om1-runtime-runbook.md)
