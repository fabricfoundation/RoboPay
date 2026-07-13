# Robot Tunnel System Client (Go)

This service keeps an outbound WebSocket tunnel to a proxy and handles request envelopes from the proxy. It exposes a local HTTP endpoint that processes x402 micropayments before dispatching robot actions over [Zenoh](https://zenoh.io/).

## What it does

- Dials the proxy WebSocket (`PROXY_WS_URL`) with the robot's ID
- Reconnects with exponential backoff (`1s`, doubling, capped at `30s`)
- Reads request envelopes continuously
- Dispatches each request in its own goroutine
- Routes by `Path` to local handlers
- Sends response envelopes with the same request `ID`
- Protects concurrent WebSocket writes with a `sync.Mutex`
- Recovers from handler panics and returns `500` response envelopes
- Verifies x402 micropayments via a configurable facilitator before running actions
- Publishes accepted action events to a Zenoh topic (`robot/tunnel/action`)
- Hot-reloads config (payee address, price, network) via Zenoh subscriber

## Envelope

```go
type Envelope struct {
    Type    string            `json:"type"`    // "request" | "response"
    ID      string            `json:"id"`
    Method  string            `json:"method,omitempty"`
    Path    string            `json:"path,omitempty"`
    Headers map[string]string `json:"headers,omitempty"`
    Status  int               `json:"status,omitempty"`
    Body    []byte            `json:"body,omitempty"` // base64 in JSON
    Error   string            `json:"error,omitempty"`
}
```

## Configuration

### Config file (`config.json`)

| Field               | Required | Default        | Description                              |
|---------------------|----------|----------------|------------------------------------------|
| `robot_id`          | No       | random UUID    | Unique robot identifier                  |
| `evm_payee_address` | **Yes**  | —              | EVM address to receive x402 payments     |
| `price`             | No       | `$0.001`       | Price per action (e.g. `$0.002`)         |
| `network`           | No       | `eip155:8453`  | CAIP-2 network ID (e.g. `eip155:84532`)  |

Example:

```json
{
  "robot_id": "my-robot",
  "evm_payee_address": "0xYourAddress",
  "price": "$0.002",
  "network": "eip155:84532"
}
```

### Environment variables

| Variable          | Default                                          | Description                          |
|-------------------|--------------------------------------------------|--------------------------------------|
| `PROXY_WS_URL`    | `wss://api.fabric.foundation/api/core/ws/robot`  | WebSocket URL of the tunnel proxy    |
| `FACILITATOR_URL` | `https://x402.org/facilitator`                   | x402 payment facilitator endpoint    |
| `GIN_MODE`        | `release`                                        | Set to `debug` for verbose HTTP logs |

## AIP agent integration (optional)

When `AIP_ENABLED=true`, the client also registers this robot as an agent on the
[Unibase AIP](https://unibase.io) network (via the [`aip-go-sdk`](https://github.com/unibaseio/aip-go-sdk)),
so AIP clients and the Butler can discover and call the robot through the gateway.

It runs in **gateway-polling mode**: the robot does **not** need a public inbound
port. AIP traffic flows `AIP → gateway (/robots/<robot_id>/…) → WebSocket tunnel
→ this client → AIP handler`, and the handler publishes accepted actions to the
same Zenoh topic (`robot/tunnel/action`) as paid x402 requests. Leave
`AIP_ENABLED` unset (or `false`) and none of the variables below are needed.

| Variable | Required* | Default | What it is & where to get it |
|----------|-----------|---------|------------------------------|
| `AIP_ENABLED` | No | `false` | `true` to register and serve as an AIP agent. Everything below is ignored when this is off. |
| `AIP_USER_ID` | **Yes** | — | The **owner wallet address** the agent is registered under (e.g. `0x1234…`). Use your operator/developer EVM wallet — the same identity as the auth token below. |
| `UNIBASE_PROXY_AUTH` | **Yes** | — | Bearer **JWT** authorizing on-chain registration. Get it by signing in with your wallet at the Unibase/Fabric auth portal (Privy). The `aip-go-sdk` can fetch it for you: its pay API's `POST /v1/init` returns an `auth_url`; open it, sign, and paste back the JWT. `PRIVY_TOKEN` is accepted as a fallback. The wallet in the token's `sub` claim must match `AIP_USER_ID`. |
| `AIP_ENDPOINT` | **Yes** | — | AIP **platform** base URL — the registration API (`POST /agents/register`). Provided by the platform operator (Fabric/Unibase). |
| `GATEWAY_URL` | **Yes** | — | AIP **gateway** base URL — where the agent polls for jobs (`/gateway/jobs/poll`) and the gateway proxies inbound calls from. Provided by the platform operator. |
| `AIP_PUBLIC_BASE_URL` | No | `https://api.fabric.foundation/api/core` | Public base used to build the robot's advertised endpoint, `<base>/robots/<robot_id>`. Keep the default for the hosted Fabric gateway; override only when self-hosting. |
| `AIP_AGENT_NAME` | No | `Robot <robot_id>` | Display name shown in the marketplace. Free choice. |
| `AIP_CHAIN_ID` | No | `97` | Chain for ERC-8004 registration: `97` = BSC testnet, `56` = BSC mainnet, `1` = Ethereum. |
| `AIP_LOCAL_PORT` | No | `8000` | Localhost port the embedded AIP server binds (internal only — it is reached through the tunnel, not exposed). |

\* Required only when `AIP_ENABLED=true`.

### Getting the credentials

1. **Wallet (`AIP_USER_ID`)** — any EVM wallet you control; this becomes the
   agent's on-chain owner. Use the same wallet for the auth token in step 2.
2. **Auth token (`UNIBASE_PROXY_AUTH`)** — a Privy-issued JWT. Either obtain it
   from the Fabric/Unibase developer portal, or let the SDK's interactive flow
   mint one: it calls `POST {pay-api}/v1/init`, prints an `auth_url`, you sign
   with your wallet in the browser, and paste the returned token back. Store it
   as `UNIBASE_PROXY_AUTH`. Treat it like a secret — never commit it.
3. **Platform & gateway URLs (`AIP_ENDPOINT`, `GATEWAY_URL`)** — these are the
   Fabric/Unibase AIP deployment endpoints. Ask the platform operator (or check
   the Fabric foundation docs) for the exact hosts for your environment
   (mainnet vs testnet). They typically live under the same domain as
   `AIP_PUBLIC_BASE_URL`.

> If you're only running the robot for paid x402 requests over the tunnel, you
> can ignore this entire section — AIP registration is purely additive.

### Run with AIP enabled (docker-compose)

```yaml
services:
  robot-tunnel-client:
    # …existing config…
    environment:
      GIN_MODE: release
      PROXY_WS_URL: wss://api.fabric.foundation/api/core/ws/robot
      FACILITATOR_URL: https://x402.org/facilitator
      AIP_ENABLED: "true"
      AIP_USER_ID: "0xYourOperatorWallet"
      UNIBASE_PROXY_AUTH: "eyJ…"            # keep this out of version control
      AIP_ENDPOINT: "https://<aip-platform-host>"
      GATEWAY_URL: "https://<aip-gateway-host>"
      # AIP_PUBLIC_BASE_URL / AIP_CHAIN_ID / AIP_AGENT_NAME use sensible defaults
```

## Local development

```bash
# Install dependencies and run tests
make test

# Build binary
make build

# Run (reads config.json by default)
make run

# Run with a custom config path
./bin/robot-tunnel-client -config /path/to/config.json
```

## Docker

### Build the image

```bash
docker build -t robot-tunnel-client .
```

### Run with the bundled config

```bash
docker run --rm \
  -e PROXY_WS_URL=wss://api.fabric.foundation/api/core/ws/robot \
  -e FACILITATOR_URL=https://x402.org/facilitator \
  robot-tunnel-client
```

### Override the config file at runtime

Mount your own `config.json` over the bundled one using `-v`:

```bash
docker run --rm \
  -v /path/to/your/config.json:/app/config.json \
  -e PROXY_WS_URL=wss://api.fabric.foundation/api/core/ws/robot \
  robot-tunnel-client
```

> **On real robot hardware** the image runs Gin in `release` mode (`GIN_MODE=release`) by default — no debug output. Override with `-e GIN_MODE=debug` only for local troubleshooting.
