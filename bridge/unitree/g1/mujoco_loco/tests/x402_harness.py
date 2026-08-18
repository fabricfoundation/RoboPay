"""Local Fabric/x402 harness for unitree-g1's real Go Tunnel integration tests.



The proxy speaks the same WebSocket envelope as Fabric, while the Tunnel

binary, its x402 middleware and its Zenoh action handoff stay real.

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



try:
    import zenoh
    HAS_ZENOH = True
except Exception:  # pragma: no cover - zenoh wheels only on Linux/macOS
    zenoh = None
    HAS_ZENOH = False


NETWORK = "eip155:84532"

PAYEE = "0x0000000000000000000000000000000000000001"





def find_tunnel_binary(root: Path) -> str | None:

    configured = os.environ.get("TUNNEL_BIN")

    candidates = [configured] if configured else []

    candidates += [str(root / "bin" / "tunnel"), str(root / "tunnel" / "tunnel_bin")]

    for candidate in candidates:

        if not candidate:

            continue

        if sys.platform == "win32" and not candidate.endswith(".exe"):

            candidate += ".exe"

        if Path(candidate).is_file():

            return candidate

    return None





def _read_exact(sock, size: int) -> bytes:

    chunks = []

    while size:

        chunk = sock.recv(size)

        if not chunk:

            raise ConnectionError("WebSocket closed while reading a frame")

        chunks.append(chunk)

        size -= len(chunk)

    return b"".join(chunks)





def _read_ws_frame(sock) -> tuple[bool, int, bytes]:

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





def _write_ws_frame(sock, payload: bytes, opcode: int = 1) -> None:

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



    def request(self, envelope: dict, timeout: float = 35) -> dict:

        payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")

        with self.write_lock:

            _write_ws_frame(self.sock, payload)



        request_id = envelope["id"]

        deadline = time.monotonic() + timeout

        while True:

            self.sock.settimeout(max(0.1, deadline - time.monotonic()))

            opcode, raw = self._read_message()

            if opcode == 8:

                raise ConnectionError("Tunnel WebSocket closed before responding")

            if opcode != 1:

                continue

            response = json.loads(raw.decode("utf-8"))

            if response.get("id") == request_id:

                return response



    def _read_message(self) -> tuple[int, bytes]:

        """Read one complete WebSocket message, including continuation frames."""

        message_opcode: int | None = None

        chunks: list[bytes] = []

        while True:

            final, opcode, raw = _read_ws_frame(self.sock)

            if opcode == 9:

                with self.write_lock:

                    _write_ws_frame(self.sock, raw, opcode=10)

                continue

            if opcode == 8:

                return opcode, raw

            if opcode in {1, 2}:

                if message_opcode is not None:

                    raise ConnectionError(

                        "received a new WebSocket message before continuation completed"

                    )

                message_opcode = opcode

            elif opcode == 0:

                if message_opcode is None:

                    raise ConnectionError(

                        "received a WebSocket continuation without an opening frame"

                    )

            else:

                continue



            chunks.append(raw)

            if final:

                return message_opcode, b"".join(chunks)





class _ProxyHandler(http.server.BaseHTTPRequestHandler):

    proxy = None



    def do_GET(self) -> None:

        clean_path = self.path.split("?", 1)[0]

        if clean_path == "/ws":

            self._handle_websocket()

            return

        if clean_path.endswith("/skills"):

            self._forward_to_tunnel("GET", "/skills", b"")

            return

        if clean_path.startswith("/robots/") and clean_path.count("/") == 2:

            self._forward_to_tunnel("GET", "/robot", b"")

            return

        if "/action/" in clean_path and clean_path.endswith("/status"):

            self._forward_to_tunnel("GET", clean_path[clean_path.index("/action/") :], b"")

            return

        self.send_error(404)



    def _handle_websocket(self) -> None:

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



    def do_POST(self) -> None:

        if not self.path.endswith("/action"):

            self.send_error(404)

            return

        content_length = int(self.headers.get("Content-Length", "0"))

        body = self.rfile.read(content_length) if content_length else b""

        self._forward_to_tunnel("POST", "/action", body)



    def _forward_to_tunnel(self, method: str, path: str, body: bytes) -> None:

        connection = self.proxy.wait_for_connection(timeout=10)

        if connection is None:

            self._write_json(503, {"error": "Tunnel is not connected to proxy"})

            return



        envelope = {

            "type": "request",

            "id": uuid.uuid4().hex,

            "method": method,

            "path": path,

            "headers": {key: value for key, value in self.headers.items() if key != "Host"},

            "body": base64.b64encode(body).decode("ascii"),

        }

        try:

            response = connection.request(envelope)

        except Exception as error:

            self._write_json(502, {"error": str(error)})

            return



        response_body = base64.b64decode(response.get("body", ""))

        self.send_response(int(response.get("status", 502)))

        for key, value in (response.get("headers") or {}).items():

            if key.lower() not in {"connection", "content-length", "transfer-encoding"}:

                self.send_header(key, value)

        self.send_header("Content-Length", str(len(response_body)))

        self.end_headers()

        self.wfile.write(response_body)



    def _write_json(self, status: int, payload: dict) -> None:

        body = json.dumps(payload).encode("utf-8")

        self.send_response(status)

        self.send_header("Content-Type", "application/json")

        self.send_header("Content-Length", str(len(body)))

        self.end_headers()

        self.wfile.write(body)



    def log_message(self, *_args) -> None:

        pass





class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):

    daemon_threads = True

    allow_reuse_address = True





class LocalFabricProxy:

    """Minimal Fabric proxy implementation for the real Tunnel protocol."""



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

    """Recording local facilitator with a configurable verification outcome."""



    calls: list[tuple[str, dict]] = []

    verify_response: dict = {

        "isValid": True,

        "payer": "0x1111111111111111111111111111111111111111",

    }



    def do_GET(self) -> None:

        if self.path != "/supported":

            self.send_error(404)

            return

        self._write_json(

            {

                "kinds": [{"x402Version": 2, "scheme": "exact", "network": NETWORK}],

                "extensions": [],

                "signers": {},

            }

        )



    def do_POST(self) -> None:

        length = int(self.headers.get("Content-Length", "0"))

        raw = self.rfile.read(length) if length else b"{}"

        self.calls.append((self.path, json.loads(raw)))

        if self.path == "/verify":

            self._write_json(self.verify_response)

        elif self.path == "/settle":

            self._write_json(

                {

                    "success": True,

                    "transaction": "0xe2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2",

                    "network": NETWORK,

                    "payer": "0x1111111111111111111111111111111111111111",

                }

            )

        else:

            self.send_error(404)



    def _write_json(self, payload: dict) -> None:

        body = json.dumps(payload).encode("utf-8")

        self.send_response(200)

        self.send_header("Content-Type", "application/json")

        self.send_header("Content-Length", str(len(body)))

        self.end_headers()

        self.wfile.write(body)



    def log_message(self, *_args) -> None:

        pass





class _ThreadingFacilitator(socketserver.ThreadingMixIn, http.server.HTTPServer):

    daemon_threads = True

    allow_reuse_address = True





def start_facilitator(verify_response: dict | None = None):

    FacilitatorHandler.calls = []

    FacilitatorHandler.verify_response = verify_response or {

        "isValid": True,

        "payer": "0x1111111111111111111111111111111111111111",

    }

    server = _ThreadingFacilitator(("127.0.0.1", 0), FacilitatorHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)

    thread.start()

    return server, thread





class ActionBoundaryObserver:

    """Records ActionEvents at the real Zenoh boundary without simulating a robot."""



    def __init__(self, action_topic: str = "robot/tunnel/action", port: int = 7447):

        config = zenoh.Config.from_json5(

            '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'

            f'"listen":{{"endpoints":["tcp/127.0.0.1:{port}"]}}}}'

        )

        self.session = zenoh.open(config)

        self._lock = threading.Lock()

        self.actions: list[dict] = []

        self.executable_commands = 0

        self.action_received = threading.Event()

        self.subscriber = self.session.declare_subscriber(action_topic, self._on_action)



    def _on_action(self, sample) -> None:

        event = json.loads(bytes(sample.payload.to_bytes()))

        with self._lock:

            self.actions.append(event)

            # Any published ActionEvent is an executable command crossing the

            # Tunnel-to-simulator boundary.

            self.executable_commands += 1

            self.action_received.set()



    def snapshot(self) -> tuple[int, int]:

        with self._lock:

            return len(self.actions), self.executable_commands



    def close(self) -> None:

        self.subscriber.undeclare()

        self.session.close()





def http_post(url: str, payload: dict, headers: dict | None = None):

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





def http_get(url: str):

    request = urllib.request.Request(url, method="GET")

    try:

        with urllib.request.urlopen(request, timeout=35) as response:

            return response.status, dict(response.headers), response.read()

    except urllib.error.HTTPError as error:

        return error.code, dict(error.headers), error.read()





def poll_action_status(status_url: str, terminal_states: set[str], timeout: float = 90) -> dict:

    deadline = time.monotonic() + timeout

    last = None

    while time.monotonic() < deadline:

        status, _, body = http_get(status_url)

        if status == 200:

            last = json.loads(body)

            if last.get("state") in terminal_states:

                return last

        time.sleep(0.5)

    raise AssertionError(f"status endpoint never reached {terminal_states}; last observation: {last}")





def payment_signature_from_402(headers: dict) -> str:

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

                "nonce": "0x" + os.urandom(32).hex(),

            },

        },

    }

    return base64.b64encode(json.dumps(payment, separators=(",", ":")).encode()).decode()

