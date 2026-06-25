# Robot Tunnel Client Compatibility

This project publishes a pre-authorized robot action envelope to:

```text
robot/tunnel/action
```

The payload is designed for a downstream bridge or tunnel client to execute without charging again.

## Envelope

```json
{
  "actionId": "act_...",
  "robotId": "g1-demo-001",
  "skillId": "move_forward",
  "params": {
    "durationSec": 3,
    "speed": 0.5
  },
  "idempotencyKey": "aeon-local-001",
  "paramsHash": "sha256(...)",
  "payment": {
    "provider": "aeon-bnb-x402",
    "network": "eip155:56",
    "asset": "USDT_OR_USDC_CONTRACT",
    "txHash": "0xmocktx",
    "payer": "0xMockPayer",
    "payTo": "0x0000000000000000000000000000000000000001",
    "amount": "10000"
  },
  "authorization": {
    "type": "local-hmac-sha256",
    "signature": "hex",
    "expiresAt": "ISO timestamp"
  },
  "issuedAt": "ISO timestamp",
  "expiresAt": "ISO timestamp"
}
```

## Expected Downstream Behavior

A downstream robot first-party service or bridge should:

- verify the action envelope signature if configured
- verify `robotId`
- verify `skillId`
- verify `paramsHash`
- verify `expiresAt`
- enforce `idempotencyKey`
- publish or execute exactly once

It should not:

- return another `402` for the same action
- call AEON `/verify` again
- call AEON `/settle` again
- charge a second time

## OM1 Skill Mapping

- `move_forward` -> `/cmd_vel.linear.x > 0`
- `turn_left` -> `/cmd_vel.angular.z > 0`
- `turn_right` -> `/cmd_vel.angular.z < 0`
- `stop` -> zero Twist
