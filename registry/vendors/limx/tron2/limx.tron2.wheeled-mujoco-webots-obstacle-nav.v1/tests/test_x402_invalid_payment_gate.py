"""Regression proof for the real Go Tunnel's x402 execution boundary.

This intentionally starts the compiled Tunnel and its real x402 middleware.
The local facilitator is a *recording* endpoint: it lets the test force a
verified or rejected payment response and observe whether ``/settle`` was
called.  It is not used as live-payment evidence; the separate Base Sepolia
job uses the public facilitator and a testnet wallet.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest
import requests
import zenoh

from limx_tron2_sim.contracts import NAVIGATION_SKILL, ROBOT_ID
from limx_tron2_sim.visual_proxy import LocalTunnelProxy, TunnelConnection


PROFILE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROFILE_ROOT.parents[4]
SKILL_CATALOG = PROFILE_ROOT / "skill-catalog.json"


def _frame(payload: bytes, opcode: int, final: bool) -> bytes:
    header = bytes([(0x80 if final else 0) | opcode])
    if len(payload) < 126:
        return header + bytes([len(payload)]) + payload
    return header + bytes([126]) + len(payload).to_bytes(2, "big") + payload


def test_websocket_reader_reassembles_continuation_frames() -> None:
    """The first paid response may be fragmented by the Fabric relay."""

    reader, writer = socket.socketpair()
    try:
        writer.sendall(
            _frame(b'{"id":"tron2-paid",', opcode=1, final=False)
            + _frame(b'"status":202}', opcode=0, final=True)
        )
        opcode, payload = TunnelConnection(reader)._read_message()
        assert opcode == 1
        assert json.loads(payload) == {"id": "tron2-paid", "status": 202}
    finally:
        reader.close()
        writer.close()
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
NETWORK = "eip155:84532"
PAYEE = "0x0000000000000000000000000000000000000001"


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RecordingFacilitator(http.server.BaseHTTPRequestHandler):
    """A small recording endpoint for required negative x402 integration tests."""

    calls: list[tuple[str, dict]] = []
    verify_response: dict = {"isValid": True, "payer": "0x1111111111111111111111111111111111111111"}

    def do_GET(self) -> None:
        if self.path != "/supported":
            self.send_error(404)
            return
        self._json({"kinds": [{"x402Version": 2, "scheme": "exact", "network": NETWORK}], "extensions": [], "signers": {}})

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")) or 0) or b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"unparseable": True}
        type(self).calls.append((self.path, payload))
        if self.path == "/verify":
            self._json(type(self).verify_response)
        elif self.path == "/settle":
            self._json(
                {
                    "success": True,
                    "transaction": "0x" + "e2" * 32,
                    "network": NETWORK,
                    "payer": "0x1111111111111111111111111111111111111111",
                }
            )
        else:
            self.send_error(404)

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


def _start_facilitator(verify_response: dict) -> tuple[_ThreadingHTTPServer, threading.Thread]:
    RecordingFacilitator.calls = []
    RecordingFacilitator.verify_response = verify_response
    server = _ThreadingHTTPServer(("127.0.0.1", 0), RecordingFacilitator)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _free_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp/127.0.0.1:{probe.getsockname()[1]}"


class _ZenohSimulator:
    """Observes real action publications and injects failure/timeout on demand."""

    def __init__(self, mode: str):
        self.endpoint = _free_endpoint()
        self.mode = mode
        self.actions: list[dict] = []
        self.state_changes = 0
        self.session = zenoh.open(
            zenoh.Config.from_json5(
                json.dumps(
                    {
                        "mode": "peer",
                        "scouting": {"multicast": {"enabled": False}},
                        "listen": {"endpoints": [self.endpoint]},
                    }
                )
            )
        )
        self.publisher = self.session.declare_publisher(RESULT_TOPIC)
        self.subscriber = self.session.declare_subscriber(ACTION_TOPIC, self._on_action)

    def _on_action(self, sample) -> None:
        event = json.loads(bytes(sample.payload.to_bytes()))
        self.actions.append(event)
        # This callback is the simulator actuation boundary: without a Zenoh
        # ActionEvent there is no command capable of changing simulator state.
        self.state_changes += 1
        if self.mode == "silent":
            return
        self.publisher.put(
            json.dumps(
                {
                    "action_id": event["action_id"],
                    "robot_id": event["robot_id"],
                    "skill_id": event["skill_id"],
                    "params_hash": event["params_hash"],
                    "idempotency_key": event["idempotency_key"],
                    "status": "failure",
                    "result": {"error_code": "INJECTED_TRON2_SIMULATOR_FAILURE"},
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def close(self) -> None:
        self.subscriber.undeclare()
        self.publisher.undeclare()
        self.session.close()


def _payment_signature(unpaid: requests.Response) -> str:
    encoded = unpaid.headers.get("PAYMENT-REQUIRED") or unpaid.headers.get("Payment-Required")
    assert encoded, "the real Tunnel must return x402 payment requirements"
    required = json.loads(base64.b64decode(encoded))
    assert required["x402Version"] == 2
    accepted = required["accepts"][0]
    payment = {
        "x402Version": 2,
        "accepted": accepted,
        "payload": {
            "signature": "0x" + "11" * 65,
            "authorization": {
                "from": "0x1111111111111111111111111111111111111111",
                "to": accepted["payTo"],
                "value": accepted["amount"],
                "validAfter": "0",
                "validBefore": str(int(time.time()) + 3600),
                "nonce": "0x" + os.urandom(32).hex(),
            },
        },
    }
    return base64.b64encode(json.dumps(payment, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _tunnel_binary() -> Path:
    configured = os.environ.get("TUNNEL_BIN")
    candidates = [Path(configured)] if configured else []
    candidates.append(REPO_ROOT / "bin" / ("tunnel.exe" if os.name == "nt" else "tunnel"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    pytest.skip("real Tunnel binary is absent; run `make build` before this integration test")


class _TunnelRun:
    def __init__(self, *, verify_response: dict, simulator_mode: str):
        self.facilitator, self.facilitator_thread = _start_facilitator(verify_response)
        self.simulator = _ZenohSimulator(simulator_mode)
        self.proxy = LocalTunnelProxy()
        self.tunnel: subprocess.Popen | None = None
        self.tempdir = tempfile.TemporaryDirectory(prefix="tron2_x402_gate_")
        self.robot_id = ROBOT_ID

    def start(self) -> None:
        self.proxy.start()
        temp = Path(self.tempdir.name)
        config = temp / "tunnel.json"
        config.write_text(
            json.dumps(
                {"robot_id": self.robot_id, "evm_payee_address": PAYEE, "price": "$0.001", "network": NETWORK}
            ),
            encoding="utf-8",
        )
        zenoh_config = temp / "zenoh.json5"
        zenoh_config.write_text(
            json.dumps(
                {
                    "mode": "client",
                    "scouting": {"multicast": {"enabled": False}},
                    "connect": {"endpoints": [self.simulator.endpoint]},
                }
            ),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            {
                "PROXY_WS_URL": f"ws://127.0.0.1:{self.proxy.port}/ws",
                "FACILITATOR_URL": f"http://127.0.0.1:{self.facilitator.server_address[1]}",
                "AIP_ENABLED": "false",
                "ZENOH_CONFIG": str(zenoh_config),
                "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                "ALLOWED_ACTIONS": f"{NAVIGATION_SKILL},stop",
                "EXECUTION_TIMEOUT_SECONDS": "2",
                "IDEMPOTENCY_STORE_PATH": str(temp / "idempotency.json"),
            }
        )
        self.tunnel = subprocess.Popen(
            [str(_tunnel_binary()), "--config", str(config)],
            cwd=REPO_ROOT,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        assert self.proxy.wait_for_connection(15) is not None, "the real Go Tunnel did not connect"
        time.sleep(0.3)

    @property
    def action_url(self) -> str:
        return f"http://127.0.0.1:{self.proxy.port}/robots/{self.robot_id}/action"

    def status_url(self, action_id: str) -> str:
        return f"http://127.0.0.1:{self.proxy.port}/robots/{self.robot_id}/action/{action_id}/status"

    def close(self) -> None:
        if self.tunnel is not None and self.tunnel.poll() is None:
            self.tunnel.terminate()
            try:
                self.tunnel.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.tunnel.kill()
        self.proxy.close()
        self.simulator.close()
        self.facilitator.shutdown()
        self.facilitator.server_close()
        self.facilitator_thread.join(timeout=5)
        self.tempdir.cleanup()


def _terminal(run: _TunnelRun, action_id: str) -> dict:
    deadline = time.monotonic() + 15
    last: dict | None = None
    while time.monotonic() < deadline:
        response = requests.get(run.status_url(action_id), timeout=5)
        if response.status_code == 200:
            last = response.json()
            if last.get("state") in {"failed", "timeout", "succeeded", "settlement_failed"}:
                return last
        time.sleep(0.2)
    raise AssertionError(f"action did not become terminal: {last}")


def _settle_calls() -> list[dict]:
    return [payload for path, payload in RecordingFacilitator.calls if path == "/settle"]


def test_facilitator_rejected_paid_shape_fails_before_action_boundary() -> None:
    """``isValid: false`` must be 402 with zero action, motion, and settlement."""

    run = _TunnelRun(
        verify_response={"isValid": False, "invalidReason": "test-tampered-payment"}, simulator_mode="fail"
    )
    try:
        run.start()
        body = {"action": NAVIGATION_SKILL, "robot_id": run.robot_id, "params": {}}
        unpaid = requests.post(run.action_url, json=body, timeout=20)
        assert unpaid.status_code == 402

        rejected = requests.post(
            run.action_url,
            json={**body, "action_id": "tampered-payment", "idempotency_key": "tampered-payment"},
            headers={"PAYMENT-SIGNATURE": _payment_signature(unpaid)},
            timeout=20,
        )
        assert rejected.status_code == 402
        time.sleep(0.8)
        assert len(run.simulator.actions) == 0, "invalid payment published an executable ActionEvent"
        assert run.simulator.state_changes == 0, "invalid payment crossed the simulator state-change boundary"
        assert not _settle_calls(), "invalid payment reached settlement"
        assert any(path == "/verify" for path, _ in RecordingFacilitator.calls)
    finally:
        run.close()


@pytest.mark.parametrize("mode, expected_state", [("fail", "failed"), ("silent", "timeout")])
def test_failed_or_timed_out_action_never_settles_and_replay_never_reexecutes(mode: str, expected_state: str) -> None:
    """Required real-Tunnel regression for failure, timeout, and durable replay."""

    run = _TunnelRun(
        verify_response={"isValid": True, "payer": "0x1111111111111111111111111111111111111111"}, simulator_mode=mode
    )
    try:
        run.start()
        unpaid = requests.post(run.action_url, json={"action": NAVIGATION_SKILL}, timeout=20)
        assert unpaid.status_code == 402
        action_id = f"tron2-no-settle-{mode}-{uuid.uuid4().hex}"
        body = {
            "action": NAVIGATION_SKILL,
            "robot_id": run.robot_id,
            "action_id": action_id,
            "idempotency_key": action_id,
            "params": {},
        }
        accepted = requests.post(
            run.action_url,
            json=body,
            headers={"PAYMENT-SIGNATURE": _payment_signature(unpaid)},
            timeout=20,
        )
        assert accepted.status_code == 202
        terminal = _terminal(run, action_id)
        assert terminal["state"] == expected_state
        assert terminal.get("settled") is False
        assert len(run.simulator.actions) == 1
        assert run.simulator.state_changes == 1
        assert not _settle_calls()

        replay = requests.post(
            run.action_url,
            json=body,
            headers={"PAYMENT-SIGNATURE": _payment_signature(unpaid)},
            timeout=20,
        )
        assert replay.status_code == 409
        assert replay.json()["error_code"] == "REPLAY_DETECTED"
        assert len(run.simulator.actions) == 1
        assert run.simulator.state_changes == 1
        assert not _settle_calls()
    finally:
        run.close()
