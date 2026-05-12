# Robot Tunnel System Client (Go)

This service keeps an outbound WebSocket tunnel to a proxy and handles request envelopes from the proxy.

## What it does

- Dials `wss://<proxy>/ws/robot?id=<robot_id>` (configured via env var)
- Reconnects with exponential backoff (`1s`, doubling, capped at `30s`)
- Reads request envelopes continuously
- Dispatches each request in its own goroutine
- Routes by `Path` to local handlers (`map[string]Handler`)
- Sends response envelopes with the same request `ID`
- Protects concurrent WebSocket writes with a `sync.Mutex`
- Recovers from handler panics and returns `500` response envelopes

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

## Config

- `ROBOT_ID` (required)
- `PROXY_WS_URL` (required, example: `wss://proxy.example.com/ws/robot`)

## Run

```bash
go mod tidy
go test ./...
ROBOT_ID=robot-123 PROXY_WS_URL=wss://proxy.example.com/ws/robot go run ./cmd
```

## Demo handlers

- `/ping` → `200`, body `pong`
- `/echo` → `200`, body = request body

Register new handlers in code:

```go
router.Register("/my/path", func(method string, headers map[string]string, body []byte) (int, map[string]string, []byte) {
    return 200, map[string]string{"content-type": "text/plain"}, []byte("ok")
})
```
