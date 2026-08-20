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
  "idempotencyKey": "xrpl-local-001"
}
```

Unpaid response:

```http
HTTP/1.1 402 Payment Required
PAYMENT-REQUIRED: <base64-json-payment-requirement>
```

Body:

```json
{
  "error": "PAYMENT_REQUIRED",
  "paymentRequired": {
    "scheme": "exact",
    "network": "xrpl:1",
    "amount": "1000",
    "asset": "XRP",
    "payTo": "rYourXrplTestnetReceiveAddress",
    "maxTimeoutSeconds": 300,
    "extra": {
      "robotId": "g1-demo-001",
      "skillId": "move_forward",
      "paramsHash": "sha256(...)",
      "idempotencyKey": "xrpl-local-001",
      "resource": "/v1/robots/g1-demo-001/actions",
      "invoiceId": "xrpl-invoice-...",
      "sourceTag": 20260601
    }
  }
}
```

Paid request headers:

- `PAYMENT-SIGNATURE: <xrpl-x402-payload>`
- `payment-signature: <xrpl-x402-payload>`
- `X-PAYMENT: <xrpl-x402-payload>`

Success:

```json
{
  "actionId": "act_...",
  "status": "accepted",
  "published": true,
  "paymentReceipt": {
    "provider": "xrpl-x402",
    "txHash": "XRPL_MOCK_TX_HASH",
    "payer": "rMockPayer",
    "payTo": "rYourXrplTestnetReceiveAddress",
    "amount": "1000",
    "asset": "XRP",
    "network": "xrpl:1",
    "robotId": "g1-demo-001",
    "skillId": "move_forward",
    "paramsHash": "sha256(...)",
    "idempotencyKey": "xrpl-local-001",
    "resource": "/v1/robots/g1-demo-001/actions",
    "invoiceId": "xrpl-invoice-...",
    "sourceTag": 20260601
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
- `invoiceId`
- `sourceTag`

Reusing a payment for a different robot, skill, params hash, idempotency key, amount, asset, network, payee, invoice, or resource returns `402 PAYMENT_BINDING_MISMATCH` and does not publish.

