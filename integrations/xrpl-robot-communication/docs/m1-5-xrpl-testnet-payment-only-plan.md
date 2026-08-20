# M1.5 XRPL Testnet Payment-Only Plan

Status: complete.

```text
M1.5 is complete.
M2 is not complete because this payment-only probe does not publish Zenoh and does not execute robot actions.
```

## Purpose

M1.5 verifies the real XRPL x402 testnet payment flow with the T54 facilitator before any robot action dispatch is enabled.

This stage must not publish Zenoh and must not execute robot actions.

## Required Inputs

```env
XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai
XRPL_NETWORK=xrpl:1
XRPL_ASSET=XRP
XRPL_AMOUNT=1000
XRPL_AMOUNT_UNIT=drops
XRPL_PAY_TO=<XRPL testnet receive address>
XRPL_BUYER_SEED=<XRPL testnet payer seed>
XRPL_TESTNET_RPC_URL=https://s.altnet.rippletest.net:51234/
XRPL_TESTNET_FAUCET_URL=https://faucet.altnet.rippletest.net/accounts
```

Keep `XRPL_BUYER_SEED` only in a local `.env` file. Never commit it.

## Commands

Terminal 1:

```bash
cd ~/workspace/XRPL-robot-communication && npm run dev:xrpl-testnet-probe
```

Terminal 2:

```bash
cd ~/workspace/XRPL-robot-communication && npm run send:xrpl-testnet
```

## Expected Result

```text
unpaid request returns 402 with PAYMENT-REQUIRED
client signs/submits XRPL testnet Payment
paid retry returns 200
PAYMENT-RESPONSE decodes to settlement result with transaction/network/payer
robotActionDispatched=false
zenohPublished=false
```

## Exit Criteria

M1.5 is complete only after a real XRPL testnet payment is observed and recorded.

## Validation Result

Environment:

```text
facilitatorUrl=https://xrpl-facilitator-testnet.t54.ai
network=xrpl:1
asset=XRP
amount=1000 drops
mode=xrpl-testnet-payment-only
```

Unpaid probe:

```text
status=402
PAYMENT-REQUIRED header present=true
```

Paid probe:

```text
status=200
ok=true
robotActionDispatched=false
zenohPublished=false
PAYMENT-RESPONSE success=true
PAYMENT-RESPONSE network=xrpl:1
PAYMENT-RESPONSE transaction=753834CB...789BB
PAYMENT-RESPONSE payer=rpYJ...S7ra
```

Security note:

```text
The XRPL testnet payer seed was used only through local environment variables.
No wallet seed is committed or recorded in this report.
```
