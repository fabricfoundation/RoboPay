# XRPL Testnet Runbook

Use this runbook only with XRPL testnet values.

## 1. Prepare Environment

```bash
cd ~/workspace/XRPL-robot-communication
cp .env.example .env
```

Set:

```env
XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai
XRPL_NETWORK=xrpl:1
XRPL_ASSET=XRP
XRPL_AMOUNT=1000
XRPL_AMOUNT_UNIT=drops
XRPL_PAY_TO=<XRPL testnet receive address>
XRPL_BUYER_SEED=<XRPL testnet payer seed>
XRPL_SKIP_FAUCET=false
```

## 2. Install and Test

```bash
npm install && npm run typecheck && npm test
```

## 3. Start Payment-Only Probe

```bash
npm run dev:xrpl-testnet-probe
```

Expected:

```text
[xrpl-testnet-probe] Starting XRPL testnet payment-only resource server.
This endpoint does not publish Zenoh and does not execute robot actions.
```

## 4. Send XRPL Testnet Payment

```bash
npm run send:xrpl-testnet
```

Expected:

```text
HTTP 200 or paid_request status 200
Decoded PAYMENT-RESPONSE contains transaction, network=xrpl:1, payer
```

## 5. Do Not Claim M2 Yet

This proves XRPL x402 testnet payment only. M2 requires a real Zenoh subscriber receiving the ActionEnvelope.

