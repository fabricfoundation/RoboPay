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
  the official Base Sepolia bridge (`L1StandardBridge.depositETH`,
  contract `0xfd0Bf71F60660E2f608ed56e1659C450eB113120` per
  docs.base.org) and the Circle testnet USDC faucet.
- Client: Python `x402` SDK (`x402ClientSync` + `ExactEvmScheme`),
  signing with the wallet above via `eth_account`.

## Flow observed

1. `POST /action` (unsigned) -> **402** Payment Required
2. Client signs a real EIP-3009 `transferWithAuthorization` for
   $0.001 USDC, retries with `PAYMENT-SIGNATURE`
3. Tunnel verifies against the real facilitator -> **202 Accepted**,
   `actionId=f332a0d7-df3e-4f33-a3f7-9f9318d983aa`
4. Bridge dispatches the real MuJoCo simulation
   (`status=success, distance_to_goal_m=0.2979`), publishes the
   terminal result on `robot/tunnel/result`
5. `ExecutionWatcher` receives the result, calls the real
   facilitator's settle endpoint -- **only now**, after success
6. `GET /action/:id/status` -> `state=succeeded, settled=true`

## On-chain proof

| Field | Value |
|---|---|
| Transaction hash | `0xa2bfff89404f026f40f8c5782fd533ca9eeaa51017804aea4be467750443bf54` |
| Explorer | https://sepolia.basescan.org/tx/0xa2bfff89404f026f40f8c5782fd533ca9eeaa51017804aea4be467750443bf54 |
| Status | SUCCESS |
| Block | 45462806 |
| Network | Base Sepolia (eip155:84532) |
| Asset | USDC (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`) |
| Amount | 1000 (smallest unit) = $0.001 USDC |

Independently re-verifiable via any Base Sepolia RPC:
```python
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('https://sepolia.base.org'))
receipt = w3.eth.get_transaction_receipt(
    '0xa2bfff89404f026f40f8c5782fd533ca9eeaa51017804aea4be467750443bf54'
)
assert receipt.status == 1
```

## Terminal logs

**localserver (tunnel):**
[GIN-debug] POST /action --> .../handlers.(*Handlers).PostAction-fm (3 handlers)
[GIN-debug] GET /action/:id/status --> .../handlers.(*Handlers).GetActionStatus-fm (3 handlers)
2026-08-14T14:50:02.593+0700 INFO localserver/main.go:104 localserver listening {"addr": ":8402"}
[GIN] 2026/08/14 - 14:51:35 | 402 | 133.345µs | 127.0.0.1 | POST "/action"
[GIN] 2026/08/14 - 14:51:36 | 202 | 527.04ms | 127.0.0.1 | POST "/action"
2026-08-14T14:51:37.858+0700 INFO localserver/main.go:85 received robot/tunnel/result, handing to ExecutionWatcher
2026-08-14T14:51:38.387+0700 INFO handlers/settlement_watcher.go:115 execution watcher: settled {"action_id": "f332a0d7-df3e-4f33-a3f7-9f9318d983aa", "transaction": "0xa2bfff89404f026f40f8c5782fd533ca9eeaa51017804aea4be467750443bf54"}
[GIN] 2026/08/14 - 14:51:38 | 200 | 39.235µs | 127.0.0.1 | GET "/action/f332a0d7-df3e-4f33-a3f7-9f9318d983aa/status"
**bridge (Python):**
[2026-08-14 14:50:49,801] [booster-k1-bridge] INFO: Opening Zenoh session...
[2026-08-14 14:50:49,821] [booster-k1-bridge] INFO: Subscribed to robot/tunnel/action, publishing results to robot/tunnel/result
[2026-08-14 14:50:49,821] [booster-k1-bridge] INFO: Bridge running. Press Ctrl+C to stop.
[2026-08-14 14:51:36,433] [booster-k1-bridge] INFO: Dispatching action_id=f332a0d7-df3e-4f33-a3f7-9f9318d983aa to MuJoCo simulator with params={'goal_x': 5.0, 'goal_y': 0.0, 'max_time_sec': 60}
[2026-08-14 14:51:37,857] [booster-k1-bridge] INFO: Published result actionId=f332a0d7-df3e-4f33-a3f7-9f9318d983aa status=success
`sim_time_sec=30.5` in the resulting metrics is simulated time (3050
physics steps x 0.01s), not wall-clock time -- MuJoCo runs this
headless and numerically, so 30.5s of simulated motion computes in
about 1.4s of real time. The metrics.json file's mtime
(`14:51:37.839`) matches the bridge's "Published result" log line to
the millisecond, confirming this is the actual output of this run,
not a stale cached file.

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
  (see `tunnel/cmd/main.go::setupRouter` vs
  `tunnel/cmd/localserver/main.go`), so the payment-gate and
  settlement logic under test is byte-for-byte the same code that
  runs in production; only the transport (real TCP vs proxied
  WebSocket) differs.
