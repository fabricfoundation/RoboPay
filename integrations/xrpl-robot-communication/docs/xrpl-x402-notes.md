# XRPL x402 Notes

XRPL x402 uses HTTP-native `402 Payment Required` semantics for agentic payments.

## Testnet Defaults

```env
XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai
XRPL_NETWORK=xrpl:1
XRPL_ASSET=XRP
XRPL_AMOUNT=1000
XRPL_AMOUNT_UNIT=drops
```

For XRP, amounts are expressed in drops. `1 XRP = 1,000,000 drops`.

## Mainnet

Mainnet should not be used until testnet has passed and a dedicated production wallet policy is approved.

```env
XRPL_FACILITATOR_URL=https://xrpl-facilitator-mainnet.t54.ai
XRPL_NETWORK=xrpl:0
```

## XRPL-Specific Binding

XRPL payments should bind an invoice to the signed transaction. This project includes `extra.invoiceId` and `extra.sourceTag` in every payment requirement and payment receipt.

The robot action gateway also binds:

- robotId
- skillId
- paramsHash
- idempotencyKey
- resource
- amount
- asset
- network
- payTo
- expiresAt

## Current Scope

M1 uses a local mock XRPL facilitator.

M1.5 uses the public XRPL testnet facilitator for payment-only verification. It does not publish Zenoh and does not execute robot actions.

