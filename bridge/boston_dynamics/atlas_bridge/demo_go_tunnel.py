"""End-to-end paid action through the repository's real Go tunnel.

``demo_tunnel.py`` exercises the Zenoh transport with a Python client. This
module goes one layer further out and drives the **actual Go tunnel binary from
this repository** — the one that mounts the upstream x402 gin middleware and a
real facilitator client — so the payment decision is made by the tunnel, not by
any Python code::

    proxy (stands in for the Fabric backend)
        -> WebSocket  ws://…/api/core/ws/robot?id=<robot_id>
        -> Go tunnel  POST /action
        -> x402 middleware  ->  live facilitator
        -> Zenoh  robot/tunnel/action
        -> Atlas bridge -> MuJoCo
        -> Zenoh  robot/tunnel/result

The tunnel connects *out* to a proxy rather than listening, so this module
implements the small envelope protocol the tunnel speaks (``internal/client.go``)
and stands in for the hosted Fabric backend. Everything from the tunnel inwards
is the real thing.

Requires the tunnel to be built once; see ``TUNNEL_BUILD.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import uuid
from pathlib import Path

from .bridge import ACTION_TOPIC, RESULT_TOPIC, ROBOT_ID, AtlasZenohBridge

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8791
PROXY_PATH = "/api/core/ws/robot"
#: How long to wait for the tunnel to dial in.
CONNECT_TIMEOUT_S = 45.0
#: How long to wait for the tunnel's HTTP response envelope.
RESPONSE_TIMEOUT_S = 90.0
#: How long to wait for the correlated simulator result on Zenoh.
RESULT_TIMEOUT_S = 240.0


def _forged_payment_header() -> str:
    """A structurally valid x402 header that was never signed."""
    payload = {
        "x402Version": 1,
        "scheme": "exact",
        "network": "base-sepolia",
        "payload": {
            "signature": "0x" + "11" * 65,
            "authorization": {
                "from": "0x520C3Ff276456A217c0dFadABeEb2d7081d6cCd4",
                "to": "0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8",
                "value": "1000",
                "validAfter": "0",
                "validBefore": "9999999999",
                "nonce": "0x" + "22" * 32,
            },
        },
    }
    return base64.b64encode(json.dumps(payload).encode()).decode()


class FabricProxy:
    """The minimal half of the Fabric backend the tunnel actually talks to."""

    def __init__(self) -> None:
        self.connection = None
        self.connected = asyncio.Event()
        self._pending: dict[str, asyncio.Future] = {}

    async def handler(self, websocket) -> None:
        import websockets

        self.connection = websocket
        self.connected.set()
        try:
            await self._pump(websocket)
        except websockets.exceptions.ConnectionClosed:
            pass  # the tunnel is torn down at the end of the run

    async def _pump(self, websocket) -> None:
        async for message in websocket:
            envelope = json.loads(message)
            if envelope.get("type") != "response":
                continue
            future = self._pending.pop(envelope.get("id", ""), None)
            if future is not None and not future.done():
                future.set_result(envelope)

    async def request(self, method: str, path: str, body: dict, headers: dict) -> dict:
        """Send one HTTP request through the tunnel and await its response."""
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self.connection.send(json.dumps({
            "type": "request",
            "id": request_id,
            "method": method,
            "path": path,
            "headers": headers,
            # Go marshals []byte as base64, and unmarshals it the same way.
            "body": base64.b64encode(json.dumps(body).encode()).decode(),
        }))
        envelope = await asyncio.wait_for(future, timeout=RESPONSE_TIMEOUT_S)
        raw = envelope.get("body")
        decoded = base64.b64decode(raw).decode() if raw else ""
        try:
            parsed = json.loads(decoded) if decoded else {}
        except json.JSONDecodeError:
            parsed = {"raw": decoded}
        return {
            "status": envelope.get("status"),
            "headers": envelope.get("headers") or {},
            "body": parsed,
        }


def _action_body(action_id: str, duration: float) -> dict:
    return {
        "action": "inspect_shelf",
        "skill_id": "inspect_shelf",
        "robot_id": ROBOT_ID,
        "action_id": action_id,
        "idempotency_key": f"idem-{action_id}",
        "params": {"maxDurationSec": duration},
    }


async def _run(tunnel_binary: Path, duration: float) -> dict:
    import websockets

    proxy = FabricProxy()
    results: dict[str, dict] = {}

    bridge = AtlasZenohBridge()
    import zenoh

    session = zenoh.open(zenoh.Config())
    session.declare_subscriber(
        RESULT_TOPIC,
        lambda sample: results.__setitem__(
            json.loads(bytes(sample.payload.to_bytes()).decode())["action_id"],
            json.loads(bytes(sample.payload.to_bytes()).decode()),
        ),
    )

    server = await websockets.serve(proxy.handler, PROXY_HOST, PROXY_PORT)
    print("=" * 70)
    print("  Atlas paid action through the real Go tunnel")
    print("=" * 70)
    print(f"  proxy listening on ws://{PROXY_HOST}:{PROXY_PORT}{PROXY_PATH}")
    print(f"  bridge listening on {ACTION_TOPIC} as {bridge.robot_id}")

    environment = dict(os.environ)
    environment["PROXY_WS_URL"] = f"ws://{PROXY_HOST}:{PROXY_PORT}{PROXY_PATH}"
    environment["PATH"] = f"{tunnel_binary.parent}{os.pathsep}{environment.get('PATH', '')}"
    tunnel = subprocess.Popen(
        [str(tunnel_binary)], cwd=str(tunnel_binary.parent), env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    steps: list[dict] = []
    try:
        await asyncio.wait_for(proxy.connected.wait(), timeout=CONNECT_TIMEOUT_S)
        print("  Go tunnel connected to the proxy\n")

        # 1. No payment at all.
        action_id = f"act-unpaid-{uuid.uuid4().hex[:8]}"
        unpaid = await proxy.request(
            "POST", "/action", _action_body(action_id, duration), {"Content-Type": "application/json"}
        )
        print(f"  [unpaid]            HTTP {unpaid['status']}")
        print(f"    payment-required : {'PAYMENT-REQUIRED' in {k.upper() for k in unpaid['headers']}}")
        steps.append({
            "step": "unpaid", "action_id": action_id,
            "http_status": unpaid["status"],
            "payment_required_header": any(
                k.upper() == "PAYMENT-REQUIRED" for k in unpaid["headers"]
            ),
            "executed": action_id in results,
            "decided_by": "go tunnel x402 middleware",
        })

        # 2. A payment the middleware will hand to the live facilitator.
        action_id = f"act-forged-{uuid.uuid4().hex[:8]}"
        forged = await proxy.request(
            "POST", "/action", _action_body(action_id, duration),
            {"Content-Type": "application/json", "PAYMENT-SIGNATURE": _forged_payment_header()},
        )
        print(f"  [forged payment]    HTTP {forged['status']}")
        detail = json.dumps(forged["body"])[:160]
        print(f"    tunnel said       : {detail}")
        steps.append({
            "step": "forged-payment", "action_id": action_id,
            "http_status": forged["status"],
            "response": forged["body"],
            "executed": action_id in results,
            "decided_by": "go tunnel x402 middleware + live facilitator",
        })

        await asyncio.sleep(3)  # give any stray action time to surface
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=20)
        except subprocess.TimeoutExpired:
            tunnel.kill()
        server.close()
        await server.wait_closed()
        session.close()
        bridge.close()

    evidence = {
        "demo": "atlas_go_tunnel_e2e",
        "tunnel": "repository Go tunnel binary (x402 gin middleware + facilitator client)",
        "proxy": "minimal stand-in for the hosted Fabric backend",
        "action_topic": ACTION_TOPIC,
        "result_topic": RESULT_TOPIC,
        "robot_id": ROBOT_ID,
        "steps": steps,
        "simulator_actions_executed": len(results),
    }
    checks = [
        ("the tunnel refused an unpaid action with HTTP 402",
         any(s["step"] == "unpaid" and s["http_status"] == 402 for s in steps)),
        ("the tunnel advertised payment requirements",
         any(s.get("payment_required_header") for s in steps)),
        ("the tunnel refused a forged payment",
         any(s["step"] == "forged-payment" and s["http_status"] in (402, 400) for s in steps)),
        ("no unpaid or forged action ever reached the simulator", len(results) == 0),
    ]
    print("\n" + "=" * 70)
    print("  INVARIANTS")
    print("=" * 70)
    for label, ok in checks:
        print(f"  [{'OK' if ok else '!!'}] {label}")
    evidence["invariants"] = {label: ok for label, ok in checks}
    evidence["all_invariants_hold"] = all(ok for _, ok in checks)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Paid Atlas action through the real Go tunnel.")
    parser.add_argument(
        "--tunnel", type=Path, required=True, help="Path to the built tunnel binary."
    )
    parser.add_argument("--max-duration", type=float, default=8.0)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    if not args.tunnel.is_file():
        raise SystemExit(f"tunnel binary not found: {args.tunnel}")

    evidence = asyncio.run(_run(args.tunnel, args.max_duration))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"\n  evidence written to {args.json_output}")
    raise SystemExit(0 if evidence["all_invariants_hold"] else 1)


if __name__ == "__main__":
    main()
