"""Real Go tunnel end-to-end: build -> boot -> WS proxy -> x402 402 -> Zenoh wire.

Exercises the ACTUAL compiled Go tunnel from ``tunnel/`` (the binary the repo
ships for production robots) rather than a Python re-implementation:

  * the tunnel dials OUT to the proxy over WS and registers as the robot,
  * an unpaid ``POST /action`` is answered by the real x402 Gin middleware
    with 402 + PAYMENT-REQUIRED (proves the paywall cannot be bypassed even
    when the facilitator is unreachable - the tunnel is useless unpaid),
  * a tunnel-format action event (the exact ``{payload, transaction_details,
    timestamp}`` envelope handlers.PostAction publishes) is consumed by
    ``robopay_link.py``: an unpaid event is honestly refused (UNPAID), a
    facilitator-signed paid event executes the skill in MuJoCo and returns a
    success result correlated by actionId on the result topic.

The WS proxy here is a mock standing in for the fabric proxy endpoint; the
envelope protocol, the x402 middleware, the Zenoh topics and the action
envelope are the real repo code.  Requires the tunnel binary to be built
first (see the go-tunnel-e2e CI job; on Linux: go build -o main cmd/main.go
with zenoh-c per tunnel/Dockerfile).  Exits nonzero on failure.

Usage:
  TUNNEL_BIN=../../tunnel/main python3 test_go_tunnel_e2e.py
"""

import argparse
import asyncio
import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

import websockets

HERE = pathlib.Path(__file__).parent
SIM_ROOT = HERE.parent
DEFAULT_TUNNEL_BIN = SIM_ROOT.parent / "tunnel" / "main"
REPORT_PATH = SIM_ROOT / "docs" / "go_tunnel_e2e_report.json"
TUNNEL_ROBOT_ID = "test-robot"
EVMPAYEE = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

sys.path.insert(0, str(HERE))

from simulate_paid_action import make_action, make_event  # noqa: E402


class MockProxy:
    """Minimal WS proxy speaking the tunnel/client.go Envelope protocol."""

    def __init__(self):
        self.connection = None
        self.robot_id = None
        self.responses = {}
        self._id_counter = 0

    def next_id(self):
        self._id_counter += 1
        return f"req_{self._id_counter}"

    async def handler(self, websocket):
        path = websocket.request.path
        query = websocket.request.query
        self.robot_id = query.get("id")
        self.connection = websocket
        print(f"[proxy] tunnel connected: path={path} robot_id={self.robot_id}",
              flush=True)
        try:
            while True:
                message = await websocket.recv()
                env = json.loads(message)
                if env.get("type") == "response":
                    self.responses[env.get("id")] = env
        except websockets.ConnectionClosed:
            pass

    async def send_request(self, method, path, headers=None, body=None):
        req_id = self.next_id()
        env = {
            "type": "request",
            "id": req_id,
            "method": method,
            "path": path,
            "headers": headers or {},
        }
        if body is not None:
            env["body"] = base64.b64encode(body).decode("ascii")
        await self.connection.send(json.dumps(env))
        deadline = time.time() + 30
        while time.time() < deadline:
            if req_id in self.responses:
                return self.responses.pop(req_id)
            await asyncio.sleep(0.1)
        raise TimeoutError(f"no response envelope for {req_id}")


async def wait_for_tunnel_connection(proxy, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proxy.connection is not None:
            return proxy
        await asyncio.sleep(0.5)
    raise TimeoutError("tunnel never connected to the proxy")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tunnel-bin", default=os.environ.get("TUNNEL_BIN")
                        or str(DEFAULT_TUNNEL_BIN))
    args = parser.parse_args()

    tunnel_bin = pathlib.Path(args.tunnel_bin)
    if not tunnel_bin.exists():
        print(f"tunnel binary not found at {tunnel_bin} - build it first "
              f"(go build per tunnel/Dockerfile)")
        sys.exit(2)
    print(f"tunnel binary: {tunnel_bin}", flush=True)

    checks = {}

    # --- mock WS proxy (fabric proxy endpoint stand-in) -----------------
    proxy = MockProxy()
    server = await websockets.serve(proxy.handler, "127.0.0.1", 0)
    proxy_url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/api/core/ws/robot"

    # --- tunnel config + env ---------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = pathlib.Path(tmp) / "config.json"
        cfg_path.write_text(json.dumps({
            "robot_id": TUNNEL_ROBOT_ID,
            "evm_payee_address": EVMPAYEE,
            "price": "$0.002",
            "network": "eip155:84532",
        }))

        env = dict(os.environ)
        env.update({
            "PROXY_WS_URL": proxy_url,
            "FACILITATOR_URL": "http://127.0.0.1:9",   # unreachable: 402 must still work
            "AIP_ENABLED": "false",
            "CHAIN": "base-sepolia",
            "GIN_MODE": "release",
            "TUNNEL_E2E": "1",
        })

        tunnel = subprocess.Popen(
            [str(tunnel_bin), "-config", str(cfg_path)],
            cwd=tmp, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        try:
            # 1) tunnel registers with the proxy
            try:
                await wait_for_tunnel_connection(proxy)
                checks["tunnel_connected_to_proxy"] = (
                    proxy.robot_id == TUNNEL_ROBOT_ID)
            except TimeoutError as exc:
                checks["tunnel_connected_to_proxy"] = False
                log_tunnel_output(tunnel)

            # 2) unpaid POST /action -> real x402 middleware 402 + PAYMENT-REQUIRED
            action = make_action("wave")
            try:
                resp = await proxy.send_request(
                    "POST", "/action", headers={"Content-Type": "application/json"},
                    body=json.dumps(action).encode("utf-8"))
                headers = {k.lower(): v for k, v in (resp.get("headers") or {}).items()}
                checks["unpaid_post_action_402"] = resp.get("status") == 402
                checks["unpaid_post_action_payment_required_header"] = (
                    "payment-required" in headers)
                checks["unpaid_post_action_body"] = (
                    "PAYMENT-REQUIRED" in (base64.b64decode(
                        resp.get("body") or b"").decode("utf-8", "replace")
                        if resp.get("body") else "").upper())
            except TimeoutError as exc:
                checks["unpaid_post_action_402"] = False
                checks["unpaid_post_action_payment_required_header"] = False
                checks["unpaid_post_action_body"] = False

            # 3) Zenoh wire interop with robopay_link.py
            import zenoh
            results = {}
            session = zenoh.open(zenoh.Config())
            session.declare_subscriber(
                "robot/tunnel/result",
                lambda s: results.setdefault(
                    json.loads(bytes(s.payload))["actionId"], []).append(
                    json.loads(bytes(s.payload))))

            link = subprocess.Popen(
                [sys.executable, "robopay_link.py", "--once"], cwd=HERE,
                env=env)
            await asyncio.sleep(3)

            # unpaid tunnel-format event -> link refuses honestly (UNPAID)
            unpaid_action = make_action("sit")
            unpaid_action.pop("payment", None)
            session.put("robot/tunnel/action",
                        json.dumps(make_event(unpaid_action)))
            await wait_for_result(results, unpaid_action["actionId"], timeout=60)
            r = results[unpaid_action["actionId"]][0]
            checks["unpaid_event_refused"] = (
                r.get("status") == "error"
                and r.get("error", {}).get("code") == "UNPAID")

            # paid tunnel-format event -> real controller -> success
            paid_action = make_action("wave")
            session.put("robot/tunnel/action",
                        json.dumps(make_event(paid_action)))
            await wait_for_result(results, paid_action["actionId"], timeout=180)
            r2 = results[paid_action["actionId"]][0]
            checks["paid_event_success"] = (
                r2.get("status") == "success"
                and r2.get("actionId") == paid_action["actionId"]
                and r2.get("skill") == "wave")

            session.close()
            link.terminate()
        finally:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
            server.close()
            await server.wait_closed()

    # --- report ----------------------------------------------------------
    report = {
        "suite": "go-tunnel-e2e",
        "tunnel_bin": str(tunnel_bin),
        "checks": checks,
        "notes": {
            "mock_ws_proxy": "stand-in for the fabric proxy WS endpoint; "
                             "envelope protocol from tunnel/internal/client.go",
            "facilitator_url": "http://127.0.0.1:9 (unreachable) - proves the "
                               "402 paywall is enforced without any facilitator",
            "real_code": "compiled Go tunnel (x402 middleware, Zenoh topics, "
                         "action envelope) + robopay_link.py (MuJoCo controller)",
            "honesty": "no live on-chain payment here; paid path uses the "
                       "simulator's local facilitator ledger (payment_gate.py)",
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=1), flush=True)
    ok = all(checks.values())
    print("PASS" if ok else "FAIL", flush=True)
    sys.exit(0 if ok else 1)


def log_tunnel_output(tunnel):
    try:
        if tunnel.stdout:
            out = tunnel.stdout.read().decode("utf-8", "replace")
            print(out[-4000:], flush=True)
    except Exception:
        pass


async def wait_for_result(results, action_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if action_id in results:
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"no result for {action_id} within {timeout}s")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
