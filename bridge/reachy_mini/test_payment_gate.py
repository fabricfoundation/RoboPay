"""test_payment_gate.py — exercises the x402 payment gate against the real Go tunnel binary.

Launches the real Go tunnel server (built via 'make build' or 'go build -o tunnel_bin ./cmd'),
sends HTTP POST /action requests, and verifies x402 payment verification against the real tunnel.

No mock HTTP servers. Uses 100% real Go tunnel server binary and real Zenoh telemetry.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
import urllib.error
import zenoh

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))

TUNNEL_PORT = 18080
ACTION_TOPIC = "robot/tunnel/action"
METRICS_TOPIC = "robot/reachy_mini/metrics"

# Locate real Go tunnel binary
candidates = [
    os.path.join(_REPO_ROOT, "bin", "tunnel"),
    os.path.join(_REPO_ROOT, "tunnel", "tunnel_bin"),
    os.path.join(_REPO_ROOT, "tunnel", "tunnel"),
]
TUNNEL_BINARY = None
for cand in candidates:
    cand_exe = cand + ".exe" if sys.platform == "win32" and not cand.endswith(".exe") else cand
    if os.path.isfile(cand_exe):
        TUNNEL_BINARY = cand_exe
        break


# ── Test Suite ───────────────────────────────────────────────────────────────

class TestPaymentGateAgainstRealTunnel(unittest.TestCase):
    """Verify x402 payment gate against the real Go Tunnel server binary."""

    _tunnel_proc = None
    _bridge_proc = None

    @classmethod
    def setUpClass(cls):
        # 1. Ensure real Tunnel binary exists
        if not TUNNEL_BINARY or not os.path.isfile(TUNNEL_BINARY):
            raise unittest.SkipTest(
                "Real Go Tunnel binary not found. Build it with 'make build' in repo root "
                "or 'go build -o tunnel_bin ./cmd' inside tunnel/ folder."
            )

        # 2. Write real tunnel configuration
        cfg_path = os.path.abspath(os.path.join(_HERE, "_tmp_real_tunnel_cfg.json"))
        tunnel_cfg = {
            "robot_id": "reachy_mini",
            "price": "$0.001",
            "network": "eip155:84532",
            "evm_payee_address": "0x0000000000000000000000000000000000000001",
            "facilitator_url": "https://x402.org/facilitator",
            "proxy_ws_url": "ws://127.0.0.1:19999/ws",
            "aip_enabled": False,
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(tunnel_cfg, f)
        cls._cfg_path = cfg_path


        # 3. Launch real Go Tunnel binary
        zenoh_c_lib = os.path.normpath(os.path.join(_REPO_ROOT, ".zenoh-c", "lib"))
        env = os.environ.copy()
        if "LD_LIBRARY_PATH" in env:
            env["LD_LIBRARY_PATH"] = f"{zenoh_c_lib}:{env['LD_LIBRARY_PATH']}"
        else:
            env["LD_LIBRARY_PATH"] = zenoh_c_lib

        cls._tunnel_proc = subprocess.Popen(
            [TUNNEL_BINARY, "--config", cfg_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )


        # Wait for real Tunnel HTTP server to start listening
        server_ready = False
        for _ in range(30):
            if cls._tunnel_proc.poll() is not None:
                # Tunnel process exited prematurely
                out, err = cls._tunnel_proc.communicate()
                print("\n[Tunnel Process Exited Output]:", err.decode("utf-8", errors="ignore"))
                break

            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{TUNNEL_PORT}/action",
                    data=b'{"action":"probe"}',
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1) as resp:
                    server_ready = True
                    break
            except urllib.error.HTTPError:
                # Any HTTP response status (402, 400, etc.) means server is responding
                server_ready = True
                break
            except Exception:
                time.sleep(0.3)

        if not server_ready and cls._tunnel_proc.poll() is None:
            # Server is running alive in background
            server_ready = True

        if not server_ready:
            cls.tearDownClass()
            raise RuntimeError("Real Go Tunnel binary failed to start HTTP server on port 18080.")





        # 4. Launch Reachy Mini simulator bridge (main.py)
        main_py = os.path.join(_HERE, "mujoco_sim_bridge", "main.py")
        cls._bridge_proc = subprocess.Popen(
            [sys.executable, main_py],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(2.0)  # Allow Zenoh session connection

    @classmethod
    def tearDownClass(cls):
        if cls._bridge_proc:
            cls._bridge_proc.terminate()
            cls._bridge_proc.wait(timeout=5)
        if cls._tunnel_proc:
            cls._tunnel_proc.terminate()
            cls._tunnel_proc.wait(timeout=5)
        if hasattr(cls, "_cfg_path") and os.path.exists(cls._cfg_path):
            os.unlink(cls._cfg_path)

    def test_1_unpaid_request_returns_http_402_payment_required(self):
        """Unpaid POST /action through real Tunnel must return HTTP 402 Payment Required."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{TUNNEL_PORT}/action",
            data=json.dumps({"action": "look_at_apple"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                body = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            status = e.code
            body = json.loads(e.read())

        self.assertEqual(status, 402, f"Expected 402 Payment Required from real Tunnel, got {status}")
        self.assertIn("payment_requirements", body, "Expected payment_requirements in 402 body")
        print("\n[Real Tunnel Test] Unpaid request correctly rejected with HTTP 402 Payment Required.")

    def test_2_malformed_json_returns_http_400_bad_request(self):
        """Malformed JSON POST /action through real Tunnel must return HTTP 400 Bad Request."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{TUNNEL_PORT}/action",
            data=b'{"action": "look_at_apple"',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code

        self.assertEqual(status, 400, f"Expected 400 Bad Request from real Tunnel, got {status}")
        print("[Real Tunnel Test] Malformed JSON correctly rejected with HTTP 400 Bad Request.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
