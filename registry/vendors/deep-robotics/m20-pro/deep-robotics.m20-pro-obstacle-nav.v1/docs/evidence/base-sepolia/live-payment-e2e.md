# Live Base Sepolia Payment -- End-to-End Proof

Real x402 payment, real facilitator (x402.org), real Base Sepolia
settlement. Not a recording facilitator, not a simulated wallet.

## Setup

- `tunnel/cmd/localserver` -- a standalone binary reusing the exact
  same router wiring as `tunnel/cmd/main.go` (`X402VerifyOnly` +
  `PostAction` + `GetActionStatus` + `ExecutionWatcher`), listening on
  a real local HTTP port instead of the Fabric WebSocket proxy (which
  this environment has no access to).
- Real facilitator: `https://x402.org/facilitator`
- Real network: `eip155:84532` (Base Sepolia)
- Real wallet: `0xE7eB3Ff85Fbe0A4e8ba79e83Be6363F53B3dbbA2`, funded via
  the official Base Sepolia bridge and the Circle testnet USDC faucet.
- Client: `tunnel/pay_m20_pro.py`, using the Python `x402` SDK
  (`x402ClientSync` + `ExactEvmScheme` + `EthAccountSigner`), signing
  with the wallet above via `eth_account`.

## Flow observed

1. `POST /action` (unsigned) -> **402** Payment Required
2. Client signs a real EIP-3009 `transferWithAuthorization` for
   $0.002 USDC, retries with `PAYMENT-SIGNATURE`
3. Tunnel verifies against the real facilitator -> **202 Accepted**,
   `actionId=be1777bb-71d0-4a8f-b053-2bb7759f3287`
4. Bridge dispatches the real MuJoCo M20 Pro simulation
   (`status=success`, `goal_reached`, `0 collisions`), publishes the
   terminal result on `robot/tunnel/result`
5. `ExecutionWatcher` receives the result, calls the real
   facilitator's settle endpoint -- **only now**, after success
6. `GET /action/:id/status` -> `state=succeeded, settled=true`

## On-chain proof

| Field | Value |
|---|---|
| Transaction hash | `0x36000cc766fc95f7f1cfe8f2500a31cc98d236e98d050738553de555f1439587` |
| Explorer | https://sepolia.basescan.org/tx/0x36000cc766fc95f7f1cfe8f2500a31cc98d236e98d050738553de555f1439587 |
| Status | SUCCESS |
| Block | 45531604 |
| Network | Base Sepolia (eip155:84532) |
| Asset | USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`) |
| Amount | 2000 (smallest unit) = $0.002 USDC |

Independently re-verifiable via any Base Sepolia RPC:

```python
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('https://sepolia.base.org'))
receipt = w3.eth.get_transaction_receipt(
    '0x36000cc766fc95f7f1cfe8f2500a31cc98d236e98d050738553de555f1439587'
)
assert receipt.status == 1
```

## Terminal logs

**localserver (tunnel):**
[GIN-debug] POST /action --> .../handlers.(*Handlers).PostAction-fm (3 handlers)
[GIN-debug] GET /action/:id/status --> .../handlers.(*Handlers).GetActionStatus-fm (3 handlers)
2026-08-16T05:04:47.771+0700 INFO localserver/main.go:104 localserver listening {"addr": ":8402"}
[GIN] 2026/08/16 - 05:04:53 | 402 | 123.216µs | 127.0.0.1 | POST "/action"
[GIN] 2026/08/16 - 05:04:54 | 202 | 396.66ms | 127.0.0.1 | POST "/action"
2026-08-16T05:04:54.375+0700 INFO localserver/main.go:85 received robot/tunnel/result, handing to ExecutionWatcher
2026-08-16T05:04:54.887+0700 INFO handlers/settlement_watcher.go:115 execution watcher: settled {"action_id": "be1777bb-71d0-4a8f-b053-2bb7759f3287", "transaction": "0x36000cc766fc95f7f1cfe8f2500a31cc98d236e98d050738553de555f1439587"}
[GIN] 2026/08/16 - 05:04:55 | 200 | 72.699µs | 127.0.0.1 | GET "/action/be1777bb-71d0-4a8f-b053-2bb7759f3287/status"
**bridge (Python):**
[2026-08-16 05:04:42,040] [m20-pro-bridge] INFO: Opening Zenoh session...
[2026-08-16 05:04:42,558] [m20-pro-bridge] INFO: Subscribed to robot/tunnel/action, publishing results to robot/tunnel/result
[2026-08-16 05:04:42,559] [m20-pro-bridge] INFO: Bridge running. Press Ctrl+C to stop.
[2026-08-16 05:04:54,326] [m20-pro-bridge] INFO: Dispatching action_id=be1777bb-71d0-4a8f-b053-2bb7759f3287 to MuJoCo simulator with params={'target_xy': [8.0, 0.0], 'max_episode_steps': 50000}
[2026-08-16 05:04:54,375] [m20-pro-bridge] INFO: Published result actionId=be1777bb-71d0-4a8f-b053-2bb7759f3287 status=success
## Notes on setup

- Payee == payer in this test (the wallet pays itself) -- this is a
  deliberate simplification to isolate and prove the payment/
  settlement *mechanism* without needing a second funded wallet; the
  facilitator interaction, signature verification, and on-chain
  settlement are identical regardless of whether payer and payee are
  the same address.
- `localserver` is not part of the production RoboPay path -- the
  real deployment goes through the Fabric WebSocket proxy
  (`tunnel/internal/client.go`), which this environment cannot reach.
  `localserver` reuses the identical `gin.Engine` router construction
  as `tunnel/cmd/main.go::setupRouter`, so the payment-gate and
  settlement logic under test is byte-for-byte the same code that
  runs in production; only the transport (real TCP vs proxied
  WebSocket) differs.
