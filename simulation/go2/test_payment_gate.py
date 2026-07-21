"""Payment-gate test: an unpaid action must be rejected by the real tunnel.

Plays the role of the cloud proxy: accepts the tunnel's websocket
connection and forwards a POST /action request WITHOUT payment through it.
The tunnel's x402 middleware must answer 402 and, crucially, nothing may
appear on the robot action topic — the robot only moves for paid actions.

Requires `pip install websockets`.
"""

import base64
import json
import pathlib
import subprocess
import sys
import threading
import time

import zenoh
from websockets.sync.server import serve

HERE = pathlib.Path(__file__).parent
REPO = HERE.parents[1]
TUNNEL = REPO / "bin" / "tunnel"
PROXY_PORT = 8765


class MockProxy:
    """Minimal stand-in for the cloud gateway's robot websocket."""

    def __init__(self):
        self.response = None
        self.connected = threading.Event()
        self.answered = threading.Event()

    def handler(self, ws):
        self.connected.set()
        request = {
            "type": "request",
            "id": "unpaid-1",
            "method": "POST",
            "path": "/action",
            "headers": {"Content-Type": "application/json"},
            "body": base64.b64encode(b'{"task": "move"}').decode(),
        }
        ws.send(json.dumps(request))
        self.response = json.loads(ws.recv(timeout=30))
        self.answered.set()
        ws.close()


def main():
    if not TUNNEL.exists():
        sys.exit(f"tunnel binary missing — run `make build` in {REPO}")

    leaked = []
    zsession = zenoh.open(zenoh.Config())
    zsession.declare_subscriber("robot/tunnel/action",
                                lambda s: leaked.append(bytes(s.payload)))

    proxy = MockProxy()
    server = serve(proxy.handler, "127.0.0.1", PROXY_PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    log = open("/tmp/tunnel_gate_test.log", "w")
    tunnel = subprocess.Popen(
        [str(TUNNEL), "-config", "tunnel/config.json"], cwd=REPO,
        env={"PATH": "/usr/bin:/bin",
             "PROXY_WS_URL": f"ws://127.0.0.1:{PROXY_PORT}/api/core/ws/robot"},
        stdout=log, stderr=log)
    checks = {}
    try:
        checks["tunnel_connected_to_proxy"] = proxy.connected.wait(30)
        checks["tunnel_answered"] = proxy.answered.wait(30)
        resp = proxy.response or {}
        checks["unpaid_action_rejected_402"] = resp.get("status") == 402
        headers = {k.upper(): v for k, v in (resp.get("headers") or {}).items()}
        checks["payment_required_advertised"] = "PAYMENT-REQUIRED" in headers
        time.sleep(2)   # would-be publish window
        checks["nothing_published_to_robot"] = leaked == []
        print(json.dumps({
            "checks": checks,
            "response_status": resp.get("status"),
            "payment_required_header":
                headers.get("PAYMENT-REQUIRED", "")[:120],
        }, indent=1))
    finally:
        tunnel.terminate()
        server.shutdown()
        zsession.close()
        log.close()

    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
