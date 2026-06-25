# AEON Staging Open Questions

Do not connect this project to real AEON staging or mainnet until these are confirmed.

## Facilitator

- What is the staging `FACILITATOR_URL`?
- Is an API key required?
- If yes, is it `Authorization: Bearer <key>` or another header?
- What is the exact auth header format and value prefix?
- Are `/verify` and `/settle` separate endpoints?
- What are the required request body fields for `/verify`?
- What are the required request body fields for `/settle`?
- Should the gateway send the full payment requirement, a compact requirement ID, or both?
- Are request timestamps, nonces, or idempotency keys required by the facilitator?

## Network And Asset

- Which `network` value should be used?
- Does AEON support BNB mainnet, BNB testnet, Base, X Layer, or another chain for this flow?
- If multiple chains are supported, which network should be used for M2 staging?
- What is the chain ID naming convention, for example `eip155:56`?
- Which token asset should be used, for example USDT or USDC?
- Which token contract address should be used?
- What are the token decimals?
- Is `amount` expressed in smallest unit or decimal unit?
- Is the amount string canonicalized?
- Does AEON expect `AMOUNT_UNIT=smallest`, decimal human units, or a token-specific format?
- Is `payTo` a static merchant wallet, a facilitator-provided address, or service-specific?

## Headers

- Is the canonical client header `payment-signature`, `PAYMENT-SIGNATURE`, or `X-PAYMENT`?
- Does AEON require accepting more than one header during migration?
- Is the `payment-required` response header expected to be JSON, base64 JSON, or another encoding?
- Should the response body duplicate the payment requirement for debugging?
- Are there AEON-specific required response headers besides `payment-required`?

## Response Schema

- What field indicates verify success?
- What field contains payer address?
- What field contains verified network?
- What field contains asset or token?
- What field indicates settle success?
- What field contains transaction hash?
- What field contains settled network?
- What field contains the settled amount?
- What field contains facilitator receipt or settlement ID?
- Are there error codes that should be mapped directly?
- Are failed verify/settle responses returned as non-2xx HTTP status, JSON booleans, or both?

## AIGateway Mapping

- How does AEON AIGateway identify this first-party service?
- Does AEON require a service registry entry for `robotId` or `resource`?
- Does AIGateway pass through idempotency keys?
- Are repeated paid requests expected to replay the same x402 payload?
- What is the AIGateway first-party service mapping mode?
- Does AEON want this robot action gateway hosted by us as the upstream service?
- Or does AEON expect to proxy/host the upstream through AEON AIGateway?
- If AEON proxies the upstream, which headers and body fields are preserved?
- Should the resource path be `/v1/robots/:robotId/actions` exactly, or an AEON gateway path?
- How should robot authorization and robot inventory be registered?
