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
