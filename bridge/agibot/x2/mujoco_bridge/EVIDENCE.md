# AGIBot X2 Tier-1 execution evidence

Verified on 2026-08-07 using canonical Base Sepolia USDC and the repository's
self-contained X2 MuJoCo model.

## Paid success

- HTTP result: `200`
- Action ID: `x2-paid-1786113858398`
- Policy: `move_forward`
- MuJoCo steps: `500`
- Simulated time: `2.0000000000000013 s`
- State delta: `0.19574685199938183`
- Root displacement: `0.19574112687206074`
- Transaction: [BaseScan receipt](https://sepolia.basescan.org/tx/0xbd51c8f3d0ff943a0c07478b1f19a569423e87c9f8376ff60700829268dfe3e5)
- Receipt status: `1` (success)
- Transfer: `2000` USDC base units (`0.002000 USDC`)
- Asset: `0x036CbD53842c5426634e7929541eC2318f3dCF7e`
- From: `0xe09729896fa906c336b2Ed36a7A08BB19E5De194`
- To: `0xd20b6ecb626DF04064771483bCD4C8aa210fbEC7`

The returned `PAYMENT-RESPONSE` decoded to:

```json
{
  "success": true,
  "payer": "0xe09729896fa906c336b2Ed36a7A08BB19E5De194",
  "transaction": "0xbd51c8f3d0ff943a0c07478b1f19a569423e87c9f8376ff60700829268dfe3e5",
  "network": "eip155:84532"
}
```

The bridge independently logged receipt, policy execution, the same state
delta, and publication of `SUCCESS` for the same action ID.

## Fail-closed evidence

A signed request missing required robot correlation fields reached the bridge
and returned HTTP `422` with `FAILED`. It had no `PAYMENT-RESPONSE`, and an
on-chain balance check remained exactly `20.000000 USDC`, proving that failed
simulation validation did not settle.

## Signed replay rejection

The successful idempotency key `x2-paid-1786113858398` was submitted again in
a fresh, correctly signed x402 request. The bridge returned:

```json
{
  "actionId": "x2-paid-1786182633383",
  "idempotencyKey": "x2-paid-1786113858398",
  "status": "REPLAY_REJECTED",
  "error": "duplicate idempotency key"
}
```

The HTTP status was `409`, `PAYMENT-RESPONSE` was absent, and the bridge log
contained only receipt of the event—no second policy execution. Independent
post-request balance checks remained `19.998000 USDC` for the payer and
`0.002000 USDC` for the payee, proving the replay was not settled.

Automated Go coverage also asserts non-2xx responses for simulator failure,
replay rejection, transport failure, and execution timeout. Python tests cover
the simulator, event parser, result schema, and replay guard.

## Reproduction safety

`cmd/paid-client` reads the payer key only from `X402_PRIVATE_KEY`, optionally
checks the derived public address, and never stores or prints the key. Local
config belongs in ignored `tunnel/config.local.json`. The direct HTTP listener
is disabled unless `LOCAL_HTTP_ADDR` is explicitly set.
