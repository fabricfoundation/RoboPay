# Architecture

This project implements an independent robot action gateway for XRPL x402 integration proof.

## Decision

The gateway is the x402 resource server and action authorization layer for robot first-party service API calls.

It performs:

- robot and skill validation
- XRPL x402 payment requirement generation
- mock XRPL facilitator `/verify`
- mock XRPL facilitator `/settle`
- payment receipt creation
- action envelope creation
- publish to `robot/tunnel/action` through a publisher abstraction

The gateway never asks the robot bridge to do a second payment, second verify, or second settle for the same action.

## Safety Flow

```text
Client
  POST /v1/robots/:robotId/actions without payment
Gateway
  402 Payment Required + PAYMENT-REQUIRED header/body
Client
  POST /v1/robots/:robotId/actions with PAYMENT-SIGNATURE
Gateway
  validate robotId, skillId, params, idempotencyKey
  verify requirement binding
  POST facilitator /verify
  POST facilitator /settle
  create PaymentReceipt
  create ActionEnvelope
  publish robot/tunnel/action
```

Any failed payment or mismatched binding stops before `PaymentReceipt`, `ActionEnvelope`, and `publish`.

## Publisher Modes

`PUBLISHER=stub` is the default. It records published payloads in memory and is used by tests and local proof.

`PUBLISHER=zenoh-cli` shells out to:

```bash
zenoh pub -k robot/tunnel/action -v '<json>'
```

If `zenoh` is unavailable, the publisher fails with a clear error. It does not silently report success.

## Boundaries

- `Aeon-robot-communication` is a reference only.
- `robopay` is not modified by this project.
- XRPL mainnet is not contacted by default.
- The M1.5 XRPL testnet probe does not publish Zenoh and does not execute robot actions.

