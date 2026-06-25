# AEON BNB x402 Notes

This repository currently uses a local mock facilitator. The code is shaped so a real facilitator can replace it through `AEON_FACILITATOR_URL` and `AEON_FACILITATOR_API_KEY`.

## Current Local Payload

The mock payment header is base64 JSON:

```json
{
  "paymentRequired": { "...": "the 402 requirement body" },
  "signature": "0xmock-payment-signature",
  "payload": {
    "authorization": {
      "from": "0xMockPayer",
      "validBefore": "ISO timestamp"
    }
  }
}
```

The gateway accepts raw JSON too, which makes local debugging easier.

## Mock Facilitator

Endpoints:

- `POST /verify`
- `POST /settle`

Success:

```json
{ "valid": true, "payer": "0xMockPayer", "network": "eip155:56" }
```

```json
{ "settled": true, "txHash": "0xmocktx", "payer": "0xMockPayer", "network": "eip155:56" }
```

Failure scenarios can be triggered with:

- `verify_failed`
- `settle_failed`
- `malformed`
- `expired`

## Real AEON Staging Alignment

Before enabling a real AEON facilitator, confirm:

- exact facilitator base URL
- API key or auth header format
- `network` value, for example `eip155:56`
- asset contract address
- token decimals
- amount unit, smallest unit vs decimal string
- canonical payment header name
- `/verify` request and response schema
- `/settle` request and response schema
- payer and transaction hash field names
- timeout and expiration semantics
