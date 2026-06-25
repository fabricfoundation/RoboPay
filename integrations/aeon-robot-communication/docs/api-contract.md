# API Contract

Base URL defaults to `http://127.0.0.1:18080`.

## Skill Catalog

`GET /v1/robots/:robotId/skills`

Success:

```json
{
  "robotId": "g1-demo-001",
  "robotType": "om1-sim-g1",
  "skills": [
    {
      "skillId": "move_forward",
      "description": "Move G1 forward for a bounded duration",
      "paramsSchema": {
        "durationSec": "number",
        "speed": "number"
      },
      "limits": {
        "maxDurationSec": 5,
        "maxSpeed": 0.5
      }
    },
    {
      "skillId": "turn_left",
      "paramsSchema": {
        "durationSec": "number",
        "angularSpeed": "number"
      }
    },
    {
      "skillId": "turn_right",
      "paramsSchema": {
        "durationSec": "number",
        "angularSpeed": "number"
      }
    },
    {
      "skillId": "stop",
      "paramsSchema": {}
    }
  ]
}
```

Wrong robot returns `404 ROBOT_NOT_FOUND`.

## Paid Action

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

Unpaid response:

```http
HTTP/1.1 402 Payment Required
payment-required: <base64-json-payment-requirement>
```

Body:

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
    "extra": {
      "robotId": "g1-demo-001",
      "skillId": "move_forward",
      "paramsHash": "sha256(...)",
      "idempotencyKey": "aeon-local-001",
      "resource": "/v1/robots/g1-demo-001/actions"
    }
  }
}
```

Paid request headers:

- `payment-signature: <mock-or-aeon-x402-payload>`
- `PAYMENT-SIGNATURE: <mock-or-aeon-x402-payload>`
- `X-PAYMENT: <mock-or-aeon-x402-payload>`

Success:

```json
{
  "actionId": "act_...",
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
    "paramsHash": "sha256(...)",
    "idempotencyKey": "aeon-local-001",
    "resource": "/v1/robots/g1-demo-001/actions"
  }
}
```

## Binding Rules

The payment requirement and receipt bind:

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

Reusing a payment for a different robot, skill, params hash, idempotency key, amount, asset, network, payee, or resource returns `402 PAYMENT_BINDING_MISMATCH`.
