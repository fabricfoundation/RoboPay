# M1 Verification Report

M1 is complete.

M2 is not complete because real Zenoh publish and OM1/ROS2 were not verified.

## Project Scope

`aeon-robot-communication` is an independent AEON BNB x402 robot action gateway. It validates a local first-party robot service API flow:

```text
AEON AIGateway / BNB x402 client
-> robot first-party service API
-> AEON x402 payment-required / payment-signature
-> mock AEON facilitator verify/settle
-> PaymentReceipt and action authorization
-> publisher abstraction for robot/tunnel/action
```

Windows M1 proof uses `StubPublisher`, so it verifies the gateway and payment flow but not real Zenoh transport.

## Repository Boundary

- Development repo: `C:\workspace\aeon-robot-communication`
- `C:\workspace\robot-tunnel-client`: read-only reference only for topic and payload compatibility
- `C:\workspace\bnb-x402`: read-only protocol reference only
- `robopay-agent-skill`: not used

No real AEON staging, AEON mainnet, Zenoh runtime, ROS2 runtime, or OM1 simulator is contacted by the M1 tests.

## Implemented API

### Skill Catalog

`GET /v1/robots/:robotId/skills`

Supported OM1 skills:

- `move_forward`
- `turn_left`
- `turn_right`
- `stop`

`wave` is intentionally unsupported.

### Paid Action

`POST /v1/robots/:robotId/actions`

Request:

```json
{
  "skillId": "move_forward",
  "params": {
    "durationSec": 3,
    "speed": 0.5
  },
  "idempotencyKey": "aeon-local-001"
}
```

Accepted payment headers:

- `payment-signature`
- `PAYMENT-SIGNATURE`
- `X-PAYMENT`

## Mock AEON Facilitator Flow

The local mock facilitator implements:

- `POST /verify`
- `POST /settle`

Success verify response:

```json
{
  "valid": true,
  "payer": "0xMockPayer",
  "network": "eip155:56"
}
```

Success settle response:

```json
{
  "settled": true,
  "txHash": "0xmocktx",
  "payer": "0xMockPayer",
  "network": "eip155:56"
}
```

Mock failure scenarios:

- `verify_failed`
- `settle_failed`
- `malformed`
- `expired`

## Unpaid 402 Response Example

```http
HTTP/1.1 402 Payment Required
payment-required: eyJzY2hlbWUiOiJleGFjdCIs...
content-type: application/json; charset=utf-8
```

```json
{
  "error": "PAYMENT_REQUIRED",
  "paymentRequired": {
    "scheme": "exact",
    "network": "eip155:56",
    "amount": "10000",
    "asset": "USDT_OR_USDC_CONTRACT",
    "payTo": "0x0000000000000000000000000000000000000001",
    "maxTimeoutSeconds": 300,
    "expiresAt": "2026-06-23T03:23:28.972Z",
    "extra": {
      "robotId": "g1-demo-001",
      "skillId": "move_forward",
      "paramsHash": "sha256(0d31a18d940ff2514cc4b4b5f770fafaa68af8f11e7abcc69e7a66cfe70df41e)",
      "idempotencyKey": "aeon-curl-unpaid-005",
      "resource": "/v1/robots/g1-demo-001/actions",
      "amount": "10000",
      "asset": "USDT_OR_USDC_CONTRACT",
      "network": "eip155:56",
      "payTo": "0x0000000000000000000000000000000000000001",
      "expiresAt": "2026-06-23T03:23:28.972Z"
    }
  }
}
```

## Paid Action Response Example

```json
{
  "actionId": "act_5242d595-4323-475c-bb7b-8fb795505555",
  "status": "accepted",
  "published": true,
  "paymentReceipt": {
    "provider": "aeon-bnb-x402",
    "txHash": "0xmocktx",
    "payer": "0xMockPayer",
    "payTo": "0x0000000000000000000000000000000000000001",
    "amount": "10000",
    "asset": "USDT_OR_USDC_CONTRACT",
    "network": "eip155:56",
    "robotId": "g1-demo-001",
    "skillId": "move_forward",
    "paramsHash": "sha256(0d31a18d940ff2514cc4b4b5f770fafaa68af8f11e7abcc69e7a66cfe70df41e)",
    "idempotencyKey": "aeon-runtime-1782184597720",
    "resource": "/v1/robots/g1-demo-001/actions",
    "expiresAt": "2026-06-23T03:21:37.753Z"
  }
}
```

## PaymentReceipt Example

```json
{
  "provider": "aeon-bnb-x402",
  "txHash": "0xmocktx",
  "payer": "0xMockPayer",
  "payTo": "0x0000000000000000000000000000000000000001",
  "amount": "10000",
  "asset": "USDT_OR_USDC_CONTRACT",
  "network": "eip155:56",
  "robotId": "g1-demo-001",
  "skillId": "move_forward",
  "paramsHash": "sha256(0d31a18d940ff2514cc4b4b5f770fafaa68af8f11e7abcc69e7a66cfe70df41e)",
  "idempotencyKey": "aeon-runtime-1782184597720",
  "resource": "/v1/robots/g1-demo-001/actions",
  "expiresAt": "2026-06-23T03:21:37.753Z"
}
```

## Generated Action Envelope Example

```json
{
  "actionId": "act_5242d595-4323-475c-bb7b-8fb795505555",
  "robotId": "g1-demo-001",
  "skillId": "move_forward",
  "params": {
    "durationSec": 3,
    "speed": 0.5
  },
  "idempotencyKey": "aeon-runtime-1782184597720",
  "paramsHash": "sha256(0d31a18d940ff2514cc4b4b5f770fafaa68af8f11e7abcc69e7a66cfe70df41e)",
  "payment": {
    "provider": "aeon-bnb-x402",
    "network": "eip155:56",
    "asset": "USDT_OR_USDC_CONTRACT",
    "txHash": "0xmocktx",
    "payer": "0xMockPayer",
    "payTo": "0x0000000000000000000000000000000000000001",
    "amount": "10000"
  },
  "issuedAt": "2026-06-23T03:16:37.794Z",
  "expiresAt": "2026-06-23T03:17:37.794Z",
  "authorization": {
    "type": "local-hmac-sha256",
    "signature": "4fdbb25d82c04e75a361a516c09c5fe28e8a3ff053002d9957b31c9438241935",
    "expiresAt": "2026-06-23T03:17:37.794Z"
  }
}
```

## Payment Binding Fields

The gateway verifies these fields before calling mock facilitator `/verify` and `/settle`:

- `robotId`
- `skillId`
- `paramsHash`
- `idempotencyKey`
- `resource`
- `amount`
- `asset`
- `network`
- `payTo`
- `expiresAt`

The same payment cannot be reused for a different robot, skill, params hash, idempotency key, amount, asset, network, payee, resource, or expiration.

## Idempotency Behavior

- First paid request publishes exactly once and returns `published: true`.
- Duplicate request with the same `idempotencyKey` and same action returns the cached action response with `published: false`.
- Duplicate request with the same `idempotencyKey` and modified params returns `409 IDEMPOTENCY_CONFLICT`.

## Negative Test Coverage

Covered by Vitest:

- unsupported skill rejected
- wrong robot rejected
- wrong skill payment reuse rejected
- modified params payment reuse rejected
- verify failure does not publish
- settle failure does not publish
- malformed payment payload does not publish
- expired requirement does not publish
- duplicate idempotency does not publish twice
- missing Zenoh CLI produces a clear publisher error

## Windows Runtime Proof Result

Executed on Windows with `PUBLISHER=stub`:

- `GET /v1/robots/g1-demo-001/skills` returned `200 OK`
- unpaid `POST /v1/robots/g1-demo-001/actions` returned `402 PAYMENT_REQUIRED`
- paid action returned `200 accepted`
- duplicate idempotency returned cached action with `published: false`
- modified params returned `409 IDEMPOTENCY_CONFLICT`
- wrong robot returned `404 ROBOT_NOT_FOUND`
- wrong skill returned `404 SKILL_NOT_FOUND`

Actual verification commands in this environment used local package binaries because `npm` was not available on PATH:

```powershell
$env:PATH="C:\Users\Junzh\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;$env:PATH"
.\node_modules\.bin\tsc.CMD -p tsconfig.json
.\node_modules\.bin\vitest.CMD run
```

Result:

```text
typecheck passed
5 test files passed
16 tests passed
```

## StubPublisher Limitation

`StubPublisher` records payloads in memory for tests and local Windows proof. It does not send data over Zenoh and cannot prove that a real subscriber receives `robot/tunnel/action`.

## Why M2 Is Not Complete

M2 is not complete because real Zenoh publish and OM1/ROS2 were not verified.

The current machine does not have `zenoh` CLI or ROS2 available. No `zenoh sub` output was observed, and no `/cmd_vel` output was observed.

## Remaining Blockers Before AEON Staging

- staging facilitator URL
- API key requirement
- auth header format
- supported network
- BNB mainnet/testnet/Base/X Layer choice
- token asset and contract address
- token decimals
- amount unit and canonical string format
- accepted payment header naming
- `/verify` request and response schema
- `/settle` request and response schema
- AIGateway first-party service mapping mode
- whether AEON expects this gateway to be hosted by us or proxied by AEON AIGateway

## Next Steps For M2

1. Move this repo to Ubuntu, WSL, or an OM1 machine with `zenoh` CLI installed.
2. Set `PUBLISHER=zenoh-cli` and `ZENOH_TOPIC=robot/tunnel/action`.
3. Run `zenoh sub -k robot/tunnel/action` in another terminal.
4. Run `npm run verify:runtime`.
5. Confirm the subscriber receives an action envelope with `skillId=move_forward`.
6. Start OM1 bridge subscribing to `robot/tunnel/action`.
7. Run `ros2 topic echo /cmd_vel`.
8. Verify `move_forward`, `turn_left`, `turn_right`, and `stop` produce the expected Twist commands.
