"""Protocol-accurate local Fabric/x402 harness for M20 integration tests.

The proxy and facilitator record protocol calls. The Go Tunnel, x402
middleware, Zenoh transport, and M20 bridge are always the real code.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import zenoh


NETWORK = "eip155:84532"
PAYEE = "0x0000000000000000000000000000000000000001"
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/deep_robotics_m20/metrics"


def find_tunnel_binary(root: Path) -> str | None:
    candidates = [os.environ.get("TUNNEL_BIN"), str(root / "bin" / "tunnel")]
    for candidate in candidates:
        if not candidate:
            continue
        if sys.platform == "win32" and not candidate.endswith(".exe"):
            candidate += ".exe"
        if Path(candidate).is_file():
            return candidate
    return None


def _read_exact(sock, size: int) -> bytes:
    chunks: list[bytes] = []
    while size:
        chunk = sock.recv(size)
        if not chunk:
            raise ConnectionError("WebSocket closed while reading a frame")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _read_frame(sock) -> tuple[bool, int, bytes]:
    first, second = _read_exact(sock, 2)
    final = bool(first & 0x80)
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
    return final, opcode, payload


def _write_frame(sock, payload: bytes, opcode: int = 1) -> None:
    header = bytes([0x80 | opcode])
    if len(payload) < 126:
        header += bytes([len(payload)])
    elif len(payload) <= 0xFFFF:
        header += bytes([126]) + len(payload).to_bytes(2, "big")
    else:
        header += bytes([127]) + len(payload).to_bytes(8, "big")
    sock.sendall(header + payload)


class TunnelConnection:
    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()

    def _read_message(self) -> tuple[int, bytes]:
        message_opcode: int | None = None
        chunks: list[bytes] = []
        while True:
            final, opcode, payload = _read_frame(self.sock)
            if opcode == 9:
                with self.lock:
                    _write_frame(self.sock, payload, opcode=10)
                continue
            if opcode == 8:
                return opcode, payload
            if opcode in {1, 2}:
                if message_opcode is not None:
                    raise ConnectionError("new WebSocket message before continuation completed")
                message_opcode = opcode
            elif opcode == 0:
                if message_opcode is None:
                    raise ConnectionError("unexpected WebSocket continuation")
            else:
                continue
            chunks.append(payload)
            if final:
                return message_opcode, b"".join(chunks)

    def request(self, envelope: dict, timeout: float = 35.0) -> dict:
        with self.lock:
            _write_frame(self.sock, json.dumps(envelope, separators=(",", ":")).encode())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.1, deadline - time.monotonic()))
            opcode, raw = self._read_message()
            if opcode == 8:
                raise ConnectionError("Tunnel WebSocket closed before responding")
            if opcode == 1:
                response = json.loads(raw)
                if response.get("id") == envelope["id"]:
                    return response
        raise TimeoutError("Tunnel response timed out")


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _ProxyHandler(http.server.BaseHTTPRequestHandler):
    proxy = None

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/ws":
            self._websocket()
        elif path.endswith("/skills"):
            self._forward("GET", "/skills", b"")
        elif path.startswith("/robots/") and path.count("/") == 2:
            self._forward("GET", "/robot", b"")
        elif "/action/" in path and path.endswith("/status"):
            self._forward("GET", path[path.index("/action/") :], b"")
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if not self.path.endswith("/action"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        self._forward("POST", "/action", self.rfile.read(length) if length else b"")

    def _websocket(self) -> None:
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
        connection = TunnelConnection(self.connection)
        self.proxy.attach(connection)
        try:
            self.proxy.stop_event.wait()
        finally:
            self.proxy.detach(connection)

    def _forward(self, method: str, path: str, body: bytes) -> None:
        connection = self.proxy.wait_for_connection(10)
        if connection is None:
            self._respond(503, b'{"error":"Tunnel is not connected"}')
            return
        envelope = {
            "type": "request",
            "id": uuid.uuid4().hex,
            "method": method,
            "path": path,
            "headers": {name: value for name, value in self.headers.items() if name != "Host"},
            "body": base64.b64encode(body).decode(),
        }
        try:
            response = connection.request(envelope)
        except Exception as error:
            self._respond(502, json.dumps({"error": str(error)}).encode())
            return
        response_body = base64.b64decode(response.get("body", ""))
        self.send_response(int(response.get("status", 502)))
        for name, value in (response.get("headers") or {}).items():
            if name.lower() not in {"connection", "content-length", "transfer-encoding"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalFabricProxy:
    def __init__(self):
        self.server = _ThreadingHTTPServer(("127.0.0.1", 0), _ProxyHandler)
        self.server.RequestHandlerClass.proxy = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.stop_event = threading.Event()
        self.connection = None
        self.condition = threading.Condition()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def start(self) -> None:
        self.thread.start()

    def attach(self, connection) -> None:
        with self.condition:
            self.connection = connection
            self.condition.notify_all()

    def detach(self, connection) -> None:
        with self.condition:
            if self.connection is connection:
                self.connection = None
                self.condition.notify_all()

    def wait_for_connection(self, timeout: float):
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.connection is None and not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.condition.wait(remaining)
            return self.connection

    def close(self) -> None:
        self.stop_event.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class FacilitatorHandler(http.server.BaseHTTPRequestHandler):
    calls: list[tuple[str, dict]] = []
    verify_response: dict = {"isValid": True, "payer": "0x1111111111111111111111111111111111111111"}

    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:
        if self.path != "/supported":
            self.send_error(404)
            return
        self._json({"kinds": [{"x402Version": 2, "scheme": "exact", "network": NETWORK}], "extensions": [], "signers": {}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) if length else b"{}")
        self.calls.append((self.path, payload))
        if self.path == "/verify":
            self._json(self.verify_response)
        elif self.path == "/settle":
            self._json({"success": True, "transaction": "0x" + "e2" * 32, "network": NETWORK})
        else:
            self.send_error(404)

    def _json(self, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_facilitator(verify_response: dict):
    FacilitatorHandler.calls = []
    FacilitatorHandler.verify_response = verify_response
    server = _ThreadingHTTPServer(("127.0.0.1", 0), FacilitatorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class ZenohObserver:
    """Real Zenoh listener used to observe action/result/metrics boundaries."""

    def __init__(self, port: int):
        self.lock = threading.Lock()
        self.actions: list[dict] = []
        self.results: list[dict] = []
        self.metrics: list[dict] = []
        self.session = zenoh.open(
            zenoh.Config.from_json5(
                '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
                f'"listen":{{"endpoints":["tcp/127.0.0.1:{port}"]}}}}'
            )
        )
        self.action_sub = self.session.declare_subscriber(ACTION_TOPIC, self._record_action)
        self.result_sub = self.session.declare_subscriber(RESULT_TOPIC, self._record_result)
        self.metrics_sub = self.session.declare_subscriber(METRICS_TOPIC, self._record_metrics)

    def _record(self, target: list[dict], sample) -> None:
        with self.lock:
            target.append(json.loads(bytes(sample.payload.to_bytes())))

    def _record_action(self, sample) -> None:
        self._record(self.actions, sample)

    def _record_result(self, sample) -> None:
        self._record(self.results, sample)

    def _record_metrics(self, sample) -> None:
        self._record(self.metrics, sample)

    def snapshot(self) -> tuple[int, int, int]:
        with self.lock:
            return len(self.actions), len(self.results), len(self.metrics)

    def close(self) -> None:
        self.action_sub.undeclare()
        self.result_sub.undeclare()
        self.metrics_sub.undeclare()
        self.session.close()


def http_post(url: str, payload: dict, headers: dict | None = None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def http_get(url: str):
    """Perform a status request through the same local Fabric proxy."""

    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def poll_action_status(url: str, terminal_states: set[str], timeout: float = 30.0) -> dict:
    """Return the correlated terminal action status or fail with the last state."""

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status, _, body = http_get(url)
        if status == 200:
            last = json.loads(body)
            if last.get("state") in terminal_states:
                return last
        time.sleep(0.25)
    raise AssertionError(f"status never reached {terminal_states}; last={last}")


def payment_signature_from_402(headers: dict) -> str:
    encoded = headers.get("PAYMENT-REQUIRED") or headers.get("Payment-Required")
    if not encoded:
        raise AssertionError("Tunnel 402 omitted PAYMENT-REQUIRED")
    requirements = json.loads(base64.b64decode(encoded))
    accepted = requirements["accepts"][0]
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
    return base64.b64encode(json.dumps(payment, separators=(",", ":")).encode()).decode()
