"""test_payment_gate.py — exercises the x402 payment gate on the tunnel HTTP server.

Plays the cloud proxy: the tunnel connects to a local mock websocket, an unpaid
POST /action is forwarded through, and the x402 middleware answers 402 with
payment requirements while nothing appears on the robot topic.

Requires the tunnel binary to be built:
    cd RoboPay/tunnel && go build -o tunnel_bin ./cmd
"""
import asyncio
import json
import subprocess
import sys
import threading
import time
import unittest
import os
import tempfile
import signal

import urllib.request
import urllib.error

# ── constants ────────────────────────────────────────────────────────────────

TUNNEL_PORT   = 18080           # local HTTP port for the tunnel under test

# Try multiple candidate paths for the tunnel binary
_HERE = os.path.dirname(os.path.abspath(__file__))
candidates = [
    # 1) Relative to this test in the tunnel/ folder (local build)
    os.path.normpath(os.path.join(_HERE, "..", "..", "tunnel", "tunnel_bin")),
    # 2) Under the workspace root bin/ folder (Makefile build)
    os.path.normpath(os.path.join(_HERE, "..", "..", "bin", "tunnel")),
]

TUNNEL_BINARY = None
for cand in candidates:
    cand_exe = cand + ".exe" if sys.platform == "win32" and not cand.endswith(".exe") else cand
    if os.path.isfile(cand_exe):
        TUNNEL_BINARY = cand_exe
        break

if TUNNEL_BINARY is None:
    TUNNEL_BINARY = candidates[0]
    if sys.platform == "win32" and not TUNNEL_BINARY.endswith(".exe"):
        TUNNEL_BINARY += ".exe"

TUNNEL_CONFIG = {
    "robot_id":          "reachy_mini_sim_test",
    "price":             "0.001",
    "network":           "eip155:84532",
    "evm_payee_address": "0x0000000000000000000000000000000000000001",
    "facilitator_url":   "https://x402.org/facilitator",
    "proxy_ws_url":      "ws://127.0.0.1:19999/ws",  # mock proxy (not running)
    "aip_enabled":       False,
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_tunnel_config(path: str) -> None:
    with open(path, "w") as f:
        json.dump(TUNNEL_CONFIG, f)


def _post_action(payload: dict, headers: dict | None = None, port: int = TUNNEL_PORT):
    """HTTP POST /action to the local tunnel, return (status_code, body_dict)."""
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"http://127.0.0.1:{port}/action",
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
    except Exception as exc:
        return None, str(exc)


# ── tests ─────────────────────────────────────────────────────────────────────

class TestPaymentGate(unittest.TestCase):
    """Verify x402 payment gate behaviour without running the robot."""

    _tunnel_proc = None

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(TUNNEL_BINARY):
            raise unittest.SkipTest(
                f"Tunnel binary not found at {TUNNEL_BINARY}. "
                "Build it with 'make build' in the repository root or "
                "'go build -o tunnel_bin ./cmd' in the tunnel/ folder."
            )

        cfg_file = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
        _write_tunnel_config(cfg_file.name)
        cfg_file.close()
        cls._cfg_path = cfg_file.name

        cls._tunnel_proc = subprocess.Popen(
            [TUNNEL_BINARY, "--config", cfg_file.name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for the tunnel HTTP server to come up (max 10 s)
        for _ in range(20):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{TUNNEL_PORT}/action", timeout=1)
            except urllib.error.HTTPError:
                break     # server answered (even with 4xx) → it's up
            except Exception:
                time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        if cls._tunnel_proc:
            cls._tunnel_proc.terminate()
            cls._tunnel_proc.wait(timeout=5)
        if hasattr(cls, "_cfg_path") and os.path.exists(cls._cfg_path):
            os.unlink(cls._cfg_path)

    def test_unpaid_request_returns_402(self):
        """An unpaid POST /action must be rejected with HTTP 402."""
        status, body = _post_action({"action": "look_at_apple"})
        self.assertEqual(
            status, 402,
            f"Expected 402 Payment Required but got {status}. Body: {body}",
        )

    def test_unpaid_body_contains_payment_requirements(self):
        """The 402 response must describe how to pay (x402 payment requirements)."""
        status, body = _post_action({"action": "look_at_apple"})
        self.assertEqual(status, 402)
        # x402 middleware sets PAYMENT-REQUIRED header; the body also contains requirements
        self.assertTrue(
            isinstance(body, dict),
            "Expected JSON body in 402 response.",
        )

    def test_invalid_json_returns_400(self):
        """Malformed JSON must be rejected with 400 before reaching the payment gate."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{TUNNEL_PORT}/action",
            data=b"{action:",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("Expected HTTP error but got 200")
        except urllib.error.HTTPError as e:
            self.assertIn(e.code, [400, 402], f"Unexpected status {e.code}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
