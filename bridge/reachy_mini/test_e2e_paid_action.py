"""Positive x402 request through the real Tunnel, proxy, Zenoh and simulator.

The production Tunnel keeps an outbound WebSocket to the Fabric proxy; it does
not expose a localhost HTTP listener. This test supplies a small stdlib-only
proxy that speaks the same JSON/WebSocket envelope used by ``tunnel/internal``.
The request therefore follows the real path::

    HTTP client -> proxy -> real Tunnel -> facilitator -> Zenoh ActionEvent
    -> Reachy Mini MuJoCo bridge -> correlated Zenoh metrics

The first request is intentionally unpaid. Its real 402 response is used to
construct a valid v2 PAYMENT-SIGNATURE, so the positive request is reproducible
without a wallet or an on-chain transaction. The local facilitator is only a
deterministic verification/settlement double; the Tunnel binary and its x402
middleware remain real.
"""
import base64
import hashlib
import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

import zenoh


_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
ACTION_TOPIC = "robot/tunnel/action"
METRICS_TOPIC = "robot/reachy_mini/metrics"
ROBOT_ID = "reachy_mini_e2e"
PAYEE = "0x0000000000000000000000000000000000000001"
NETWORK = "eip155:84532"


def _find_tunnel_binary():
    candidates = [
        os.path.join(_ROOT, "bin", "tunnel"),
        os.path.join(_ROOT, "tunnel", "tunnel_bin"),
    ]
    for candidate in candidates:
        if sys.platform == "win32" and not candidate.endswith(".exe"):
            candidate += ".exe"
        if os.path.isfile(candidate):
            return candidate
    return None


def _read_exact(sock, size):
    chunks = []
    while size:
        chunk = sock.recv(size)
        if not chunk:
            raise ConnectionError("WebSocket closed while reading a frame")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _read_ws_frame(sock):
    first, second = _read_exact(sock, 2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(sock, 8), "big")
    mask = _read_exact(sock, 4) if masked else None
    payload = _read_exact(sock, length) if length else b""
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return opcode, payload


def _write_ws_frame(sock, payload, opcode=1):
    header = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header += bytes([length])
    elif length <= 0xFFFF:
        header += bytes([126]) + length.to_bytes(2, "big")
    else:
        header += bytes([127]) + length.to_bytes(8, "big")
    sock.sendall(header + payload)


class _TunnelConnection:
    def __init__(self, sock):
        self.sock = sock
        self.write_lock = threading.Lock()

    def request(self, envelope, timeout=35):
        payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        with self.write_lock:
            _write_ws_frame(self.sock, payload)

        request_id = envelope["id"]
        deadline = time.monotonic() + timeout
        while True:
            self.sock.settimeout(max(0.1, deadline - time.monotonic()))
            opcode, raw = _read_ws_frame(self.sock)
            if opcode == 9:  # ping
                with self.write_lock:
                    _write_ws_frame(self.sock, raw, opcode=10)
                continue
            if opcode == 8:
                raise ConnectionError("Tunnel WebSocket closed before responding")
            if opcode != 1:
                continue
            response = json.loads(raw.decode("utf-8"))
            if response.get("id") == request_id:
                return response


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    proxy = None

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/ws":
            self.send_error(404)
            return

        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "missing Sec-WebSocket-Key")
            return

        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()

        connection = _TunnelConnection(self.connection)
        self.proxy.attach(connection)
        try:
            self.proxy.stop_event.wait()
        finally:
            self.proxy.detach(connection)

    def do_POST(self):
        if not self.path.endswith("/action"):
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else b""
        connection = self.proxy.wait_for_connection(timeout=10)
        if connection is None:
            self._write_json(503, {"error": "Tunnel is not connected to proxy"})
            return

        request_id = uuid.uuid4().hex
        headers = {key: value for key, value in self.headers.items()}
        headers.pop("Host", None)
        envelope = {
            "type": "request",
            "id": request_id,
            "method": "POST",
            "path": "/action",
            "headers": headers,
            # Go's []byte JSON representation is base64 encoded.
            "body": base64.b64encode(body).decode("ascii"),
        }
        try:
            response = connection.request(envelope)
        except Exception as exc:
            self._write_json(502, {"error": str(exc)})
            return

        response_body = base64.b64decode(response.get("body", ""))
        status = int(response.get("status", 502))
        self.send_response(status)
        for key, value in (response.get("headers") or {}).items():
            if key.lower() not in {"connection", "content-length", "transfer-encoding"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def _write_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class LocalFabricProxy:
    """Minimal local Fabric proxy for the real Tunnel WebSocket protocol."""

    def __init__(self):
        self.server = _ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        self.server.RequestHandlerClass.proxy = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.stop_event = threading.Event()
        self.connection = None
        self.condition = threading.Condition()

    @property
    def port(self):
        return self.server.server_address[1]

    def start(self):
        self.thread.start()

    def attach(self, connection):
        with self.condition:
            self.connection = connection
            self.condition.notify_all()

    def detach(self, connection):
        with self.condition:
            if self.connection is connection:
                self.connection = None
                self.condition.notify_all()

    def wait_for_connection(self, timeout):
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.connection is None and not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.connection

    def close(self):
        self.stop_event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class _FacilitatorHandler(http.server.BaseHTTPRequestHandler):
    calls = []

    def do_GET(self):
        if self.path != "/supported":
            self.send_error(404)
            return
        response = {
            "kinds": [
                {"x402Version": 2, "scheme": "exact", "network": NETWORK}
            ],
            "extensions": [],
            "signers": {},
        }
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        self.calls.append((self.path, json.loads(raw)))

        if self.path == "/verify":
            response = {
                "isValid": True,
                "payer": "0x1111111111111111111111111111111111111111",
            }
        elif self.path == "/settle":
            response = {
                "success": True,
                "transaction": "0xe2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2",
                "network": NETWORK,
                "payer": "0x1111111111111111111111111111111111111111",
            }
        else:
            self.send_error(404)
            return

        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class _ThreadingFacilitator(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _start_facilitator():
    _FacilitatorHandler.calls = []
    server = _ThreadingFacilitator(("127.0.0.1", 0), _FacilitatorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _http_post(url, payload, headers=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _payment_signature_from_402(headers):
    encoded = headers.get("PAYMENT-REQUIRED") or headers.get("Payment-Required")
    if not encoded:
        raise AssertionError("real Tunnel 402 did not include PAYMENT-REQUIRED")
    required = json.loads(base64.b64decode(encoded))
    if required.get("x402Version") != 2:
        raise AssertionError(f"expected x402 v2 requirements, got {required}")
    accepted = required["accepts"][0]
    payment = {
        "x402Version": 2,
        "accepted": accepted,
        "payload": {
            "signature": "0x" + ("11" * 65),
            "authorization": {
                "from": "0x1111111111111111111111111111111111111111",
                "to": accepted["payTo"],
                "value": accepted["amount"],
                "validAfter": "0",
                "validBefore": str(int(time.time()) + 3600),
                "nonce": "0x" + ("00" * 32),
            },
        },
    }
    return base64.b64encode(json.dumps(payment, separators=(",", ":")).encode()).decode()


class TestEndToEndPaidAction(unittest.TestCase):
    """HTTP paid request -> real Tunnel -> simulator -> correlated metrics."""

    def test_positive_paid_request_drives_simulator(self):
        tunnel_binary = _find_tunnel_binary()
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with: make build")

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = _start_facilitator()
        bridge_proc = None
        tunnel_proc = None
        cfg_path = None
        zenoh_cfg_path = None
        z_session = None
        action_sub = None
        metrics_sub = None

        try:
            proxy.start()
            cfg = {
                "robot_id": ROBOT_ID,
                "evm_payee_address": PAYEE,
                "price": "$0.001",
                "network": NETWORK,
            }
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", prefix="reachy_e2e_", delete=False
            ) as config_file:
                json.dump(cfg, config_file)
                cfg_path = config_file.name

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json5", prefix="reachy_zenoh_", delete=False
            ) as zenoh_file:
                json.dump(
                    {
                        "mode": "peer",
                        "scouting": {"multicast": {"enabled": False}},
                        "connect": {"endpoints": ["tcp/127.0.0.1:7447"]},
                    },
                    zenoh_file,
                )
                zenoh_cfg_path = zenoh_file.name

            child_env = os.environ.copy()
            child_env["PROXY_WS_URL"] = f"ws://127.0.0.1:{proxy.port}/ws"
            child_env["FACILITATOR_URL"] = f"http://127.0.0.1:{facilitator.server_address[1]}"
            child_env["AIP_ENABLED"] = "false"
            child_env["ZENOH_CONFIG"] = zenoh_cfg_path

            tunnel_proc = subprocess.Popen(
                [tunnel_binary, "--config", cfg_path],
                cwd=_ROOT,
                env=child_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
            )
            self.assertIsNotNone(
                proxy.wait_for_connection(15),
                "real Tunnel did not connect to the local Fabric proxy",
            )

            # The reviewer path must be able to use the real ROS2 launch.  Set
            # REACHY_BRIDGE_EXTERNAL=1 after starting `make bridge-run` to
            # keep this test focused on the paid Tunnel -> Zenoh handoff.
            if os.environ.get("REACHY_BRIDGE_EXTERNAL") != "1":
                main_py = os.path.join(_HERE, "mujoco_sim_bridge", "main.py")
                bridge_env = os.environ.copy()
                bridge_env["QT_QPA_PLATFORM"] = "offscreen"
                if os.path.isfile("/opt/webots/webots"):
                    bridge_env["WEBOTS_EXE"] = "/opt/webots/webots"
                bridge_proc = subprocess.Popen(
                    [sys.executable, main_py],
                    cwd=_HERE,
                    env=bridge_env,
                )

            z_config = zenoh.Config.from_json5(
                '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
                '"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}'
            )
            z_session = zenoh.open(z_config)
            action_events = []
            metrics = []
            action_received = threading.Event()
            metrics_received = threading.Event()

            def on_action(sample):
                event = json.loads(bytes(sample.payload.to_bytes()))
                action_events.append(event)
                action_received.set()

            def on_metrics(sample):
                result = json.loads(bytes(sample.payload.to_bytes()))
                metrics.append(result)
                metrics_received.set()

            action_sub = z_session.declare_subscriber(ACTION_TOPIC, on_action)
            metrics_sub = z_session.declare_subscriber(METRICS_TOPIC, on_metrics)
            time.sleep(1.0)

            public_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"
            unpaid_status, unpaid_headers, _ = _http_post(
                public_url, {"action": "look_at_apple"}
            )
            self.assertEqual(unpaid_status, 402)
            payment_signature = _payment_signature_from_402(unpaid_headers)

            request_id = f"reachy-e2e-{uuid.uuid4().hex}"
            paid_payload = {
                "action": "look_at_apple",
                "params": {
                    "duration": 4.0,
                    "target_object": "apple",
                    "request_id": request_id,
                },
            }
            paid_status, _, paid_body = _http_post(
                public_url,
                paid_payload,
                {"PAYMENT-SIGNATURE": payment_signature},
            )
            response_body = json.loads(paid_body)
            print(f"\n[E2E] paid request status={paid_status} body={response_body}")

            self.assertEqual(paid_status, 200)
            self.assertEqual(response_body.get("status"), "accepted")
            self.assertTrue(action_received.wait(5), "Tunnel did not publish ActionEvent")
            self.assertTrue(metrics_received.wait(60), "simulator metrics not received")

            event = next(
                event for event in action_events
                if event.get("payload", {}).get("params", {}).get("request_id") == request_id
            )
            result = next(item for item in metrics if item.get("correlation_id") == request_id)
            self.assertEqual(event["payload"]["action"], "look_at_apple")
            self.assertEqual(result["execution_status"], "SUCCESS")
            self.assertTrue(result["metrics"]["task_completed"])
            self.assertGreaterEqual(result["metrics"]["tracking_success_rate"], 0.9)
            self.assertGreaterEqual(
                result["sim_to_sim_validation"]["overall_sim2sim_robustness_score"], 0.9
            )
            self.assertGreaterEqual(
                sum(1 for path, _ in _FacilitatorHandler.calls if path == "/verify"), 1
            )
            self.assertGreaterEqual(
                sum(1 for path, _ in _FacilitatorHandler.calls if path == "/settle"), 1
            )
            print(f"[E2E] correlated simulator metrics={json.dumps(result, indent=2)}")
        finally:
            if metrics_sub is not None:
                metrics_sub.undeclare()
            if action_sub is not None:
                action_sub.undeclare()
            if z_session is not None:
                z_session.close()
            if bridge_proc is not None:
                try:
                    bridge_proc.terminate()
                    bridge_proc.wait(timeout=2)
                except Exception:
                    bridge_proc.kill()
            if tunnel_proc is not None:
                try:
                    tunnel_proc.terminate()
                    tunnel_proc.wait(timeout=2)
                except Exception:
                    tunnel_proc.kill()
            proxy.close()
            facilitator.shutdown()
            facilitator.server_close()
            facilitator_thread.join(timeout=5)
            if cfg_path and os.path.exists(cfg_path):
                os.unlink(cfg_path)
            if zenoh_cfg_path and os.path.exists(zenoh_cfg_path):
                os.unlink(zenoh_cfg_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
