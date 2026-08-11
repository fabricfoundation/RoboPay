"""Operator-only HTTP/WebSocket proxy for recording a real Tunnel E2E.

It forwards byte-for-byte HTTP envelopes to the real Go Tunnel. It does not
emulate x402, the facilitator, Zenoh, or either simulator.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import socket
import socketserver
import threading
import time
import uuid
from typing import Any


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    while size:
        chunk = sock.recv(size)
        if not chunk:
            raise ConnectionError("WebSocket closed while reading a frame")
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)


def _read_frame(sock: socket.socket) -> tuple[bool, int, bytes]:
    first, second = _read_exact(sock, 2)
    final, opcode = bool(first & 0x80), first & 0x0F
    masked, length = bool(second & 0x80), second & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(sock, 8), "big")
    mask = _read_exact(sock, 4) if masked else None
    payload = _read_exact(sock, length) if length else b""
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return final, opcode, payload


def _write_frame(sock: socket.socket, payload: bytes, opcode: int = 1) -> None:
    header = bytes([0x80 | opcode])
    if len(payload) < 126:
        header += bytes([len(payload)])
    elif len(payload) <= 0xFFFF:
        header += bytes([126]) + len(payload).to_bytes(2, "big")
    else:
        header += bytes([127]) + len(payload).to_bytes(8, "big")
    sock.sendall(header + payload)


class TunnelConnection:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.lock = threading.Lock()

    def _read_message(self) -> tuple[int, bytes]:
        opcode_start: int | None = None
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
                if opcode_start is not None:
                    raise ConnectionError("new message before continuation completed")
                opcode_start = opcode
            elif opcode == 0:
                if opcode_start is None:
                    raise ConnectionError("unexpected WebSocket continuation")
            else:
                continue
            chunks.append(payload)
            if final:
                return int(opcode_start), b"".join(chunks)

    def request(self, envelope: dict[str, Any], timeout: float = 45.0) -> dict[str, Any]:
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


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _Handler(http.server.BaseHTTPRequestHandler):
    proxy: "LocalTunnelProxy"

    def log_message(self, fmt: str, *args: Any) -> None:
        self.proxy.log(f"[proxy] {fmt % args}")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/ws":
            self._websocket()
        elif path.endswith("/skills"):
            self._forward("GET", "/skills", b"")
        elif path.startswith("/robots/") and path.count("/") == 2:
            self._forward("GET", "/robot", b"")
        elif "/action/" in path and path.endswith("/status"):
            self._forward("GET", path[path.index("/action/"):], b"")
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
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
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
        connection = self.proxy.wait_for_connection(15)
        if connection is None:
            self._respond(503, b'{"error":"Tunnel is not connected"}')
            return
        envelope = {
            "type": "request", "id": uuid.uuid4().hex, "method": method, "path": path,
            "headers": {k: v for k, v in self.headers.items() if k.lower() != "host"},
            "body": base64.b64encode(body).decode("ascii"),
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


class LocalTunnelProxy:
    def __init__(self, *, verbose: bool = False):
        self.verbose = verbose
        self.server = _Server(("0.0.0.0", 0), _Handler)
        self.server.RequestHandlerClass.proxy = self
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.stop_event = threading.Event()
        self.connection: TunnelConnection | None = None
        self.condition = threading.Condition()

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def start(self) -> None:
        self.thread.start()

    def attach(self, connection: TunnelConnection) -> None:
        with self.condition:
            self.connection = connection
            self.condition.notify_all()
        self.log("[proxy] real Tunnel connected")

    def detach(self, connection: TunnelConnection) -> None:
        with self.condition:
            if self.connection is connection:
                self.connection = None
                self.condition.notify_all()

    def wait_for_connection(self, timeout: float) -> TunnelConnection | None:
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
