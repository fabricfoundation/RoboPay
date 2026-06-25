# Architecture

This project implements an independent robot action gateway for AEON BNB x402 integration proof.

## Decision

The gateway is the x402 resource server and action authorization layer for robot first-party service API calls.

It performs:

- robot and skill validation
- payment requirement generation
- AEON mock or real facilitator `/verify`
- AEON mock or real facilitator `/settle`
- payment receipt creation
- action envelope creation
- publish to `robot/tunnel/action` through a publisher abstraction

The gateway never asks `robot-tunnel-client` to do a second payment, second verify, or second settle for the same action.

## Local Proof Flow

```text
Client
  POST /v1/robots/:robotId/actions without payment
Gateway
  402 Payment Required + payment-required header/body
Client
  POST /v1/robots/:robotId/actions with payment-signature
Gateway
  validate robotId, skillId, params, idempotencyKey
  verify requirement binding
  POST mock facilitator /verify
  POST mock facilitator /settle
  create PaymentReceipt
  create ActionEnvelope
  publish robot/tunnel/action
```

## Publisher Modes

`PUBLISHER=stub` is the default. It records published payloads in memory and is used by tests and Windows local proof.

`PUBLISHER=zenoh-cli` shells out to:

```bash
zenoh pub -k robot/tunnel/action -v '<json>'
```

If `zenoh` is unavailable, the publisher fails with a clear error. It does not silently report success.

## Boundaries

- `robot-tunnel-client` is a compatibility reference only.
- `bnb-x402` is a protocol reference only.
- `robopay-agent-skill` is not used.
- Real AEON staging or mainnet is not contacted by default.
