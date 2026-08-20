# M1 Verification Report

Status: complete.

```text
M1 is complete.
M1.5 is not complete because XRPL testnet wallet/facilitator execution has not been run yet.
M2 is not complete because real Zenoh publish and OM1/ROS2 were not verified in this repo yet.
```

## Scope

M1 verifies the local mock XRPL x402 robot action gateway.

## Verification Result

```text
- typecheck passed
- 5 test files passed
- 21 tests passed
- unpaid requests return 402
- paid mock requests return accepted action response
- PaymentReceipt binds action and payment fields
- ActionEnvelope is generated only after successful payment
- StubPublisher captures exactly one payload for successful payment
- all negative payment paths do not publish
```

## Required Negative Coverage

- unpaid -> 402 -> no publish
- verify failed -> no publish
- settle failed -> no publish
- malformed payment -> no publish
- expired requirement -> no publish
- wrong robotId -> no publish
- wrong skillId -> no publish
- modified paramsHash -> no publish
- amount mismatch -> no publish
- asset mismatch -> no publish
- network mismatch -> no publish
- payTo mismatch -> no publish
- invoiceId mismatch -> no publish
- duplicate idempotencyKey same params -> published=false
- duplicate idempotencyKey different params -> 409

## Commands Used

Windows PATH did not include `npm`, so the local Codex bundled Node runtime was used:

```powershell
& 'C:\Users\Junzh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' .\node_modules\typescript\bin\tsc -p tsconfig.json
& 'C:\Users\Junzh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' .\node_modules\vitest\vitest.mjs run
```

## Runtime Limitation

This M1 proof uses `PUBLISHER=stub`. It does not prove real Zenoh publish or OM1/ROS2 execution.

## Local Runtime Proof

Commands:

```powershell
$env:MOCK_XRPL_PORT='3402'; pnpm run dev:mock-xrpl
$env:PORT='18080'; $env:ROBOT_ID='g1-demo-001'; $env:XRPL_FACILITATOR_URL='http://127.0.0.1:3402'; $env:PUBLISHER='stub'; pnpm run dev
$env:GATEWAY_URL='http://127.0.0.1:18080'; pnpm run verify:runtime
```

Result:

```text
skills status 200
unpaid status 402
paid status 200
duplicate status 200, published=false
modifiedParams status 409
wrongRobot status 404
wrongSkill status 404
```

The paid response contained `payment.provider=xrpl-x402`, `txHash=XRPL_MOCK_TX_HASH`, `payer=rMockPayer`, `invoiceId`, `sourceTag`, and a signed `local-hmac-sha256` ActionEnvelope.
