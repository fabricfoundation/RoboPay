# AEON Robot Communication

Independent AEON BNB x402 robot action gateway for local integration proof.

This repository is self-contained. It does not depend on `robopay-agent-skill`, and it does not modify `robot-tunnel-client` or `bnb-x402`.

## What This Proves

Local M1 proof scope:

```text
AEON AIGateway / BNB x402 client
-> robot first-party service API
-> AEON x402 payment-required / payment-signature
-> mock AEON facilitator verify/settle
-> payment receipt and action authorization
-> StubPublisher capture for robot/tunnel/action
```

Windows default runtime uses `PUBLISHER=stub`. It does not prove real Zenoh or OM1 movement. Real Zenoh and ROS2 `/cmd_vel` must be verified on Ubuntu, WSL, or an OM1 machine.

## Setup

```powershell
cd C:\workspace\aeon-robot-communication
npm install
npm run typecheck
npm test
```

If this machine has no `npm` or `node` on PATH, use the bundled Codex runtime Node and the package binaries:

```powershell
$env:PATH="C:\Users\Junzh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;$env:PATH"
.\node_modules\.bin\tsc.CMD -p tsconfig.json
.\node_modules\.bin\vitest.CMD run
```

## Local Runtime

Terminal 1:

```powershell
cd C:\workspace\aeon-robot-communication
$env:MOCK_AEON_PORT="3402"
npm run dev:mock-aeon
```

Terminal 2:

```powershell
cd C:\workspace\aeon-robot-communication
$env:PORT="18080"
$env:ROBOT_ID="g1-demo-001"
$env:AEON_FACILITATOR_URL="http://127.0.0.1:3402"
$env:PUBLISHER="stub"
npm run dev
```

Skills:

```powershell
curl.exe -i http://127.0.0.1:18080/v1/robots/g1-demo-001/skills
```

Unpaid action:

```powershell
'{"skillId":"move_forward","params":{"durationSec":3,"speed":0.5},"idempotencyKey":"aeon-local-001"}' | curl.exe -i -X POST http://127.0.0.1:18080/v1/robots/g1-demo-001/actions `
  -H "content-type: application/json" `
  --data-binary "@-"
```

Paid action helper:

```powershell
$env:GATEWAY_URL="http://127.0.0.1:18080"
$env:ROBOT_ID="g1-demo-001"
$env:SKILL_ID="move_forward"
$env:IDEMPOTENCY_KEY="aeon-local-001"
npm run send:paid
```

Full local HTTP proof:

```powershell
$env:GATEWAY_URL="http://127.0.0.1:18080"
npm run verify:runtime
```

`npm run runtime:verify` is kept as a compatibility alias.

## Real Zenoh Preparation

On Ubuntu, WSL, or an OM1 machine, install or make the `zenoh` CLI available on PATH, then set:

```bash
export PUBLISHER=zenoh-cli
export ZENOH_TOPIC=robot/tunnel/action
```

When `PUBLISHER=zenoh-cli`, the gateway publishes:

```bash
zenoh pub -k robot/tunnel/action -v '<json action envelope>'
```

If the `zenoh` CLI is missing or the command exits non-zero, the gateway returns a clear publish error instead of silently succeeding.

## Supported Skills

- `move_forward`: `{ "durationSec": number, "speed": number }`
- `turn_left`: `{ "durationSec": number, "angularSpeed": number }`
- `turn_right`: `{ "durationSec": number, "angularSpeed": number }`
- `stop`: `{}`

`wave` is intentionally not supported.

## Docs

- [Architecture](docs/architecture.md)
- [API Contract](docs/api-contract.md)
- [AEON BNB x402 Notes](docs/aeon-bnb-x402-notes.md)
- [M1 Verification Report](docs/m1-verification-report.md)
- [M2 AEON Zenoh OM1 Validation Report](docs/m2-aeon-zenoh-om1-validation-report.md)
- [M2 Runbook](docs/m2-runbook.md)
- [Robot Tunnel Client Compatibility](docs/robot-tunnel-client-compatibility.md)
- [OM1 Runtime Runbook](docs/om1-runtime-runbook.md)
- [AEON Staging Open Questions](docs/aeon-staging-open-questions.md)
