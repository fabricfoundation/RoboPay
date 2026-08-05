"""test_payment_gate.py — exercises the x402 payment gate against the real Go tunnel binary.

Launches the real Go tunnel server (built via 'make build' or 'go build -o tunnel_bin ./cmd'),
sends HTTP POST /action requests, and verifies x402 payment verification against the real tunnel.

Uses the real Go Tunnel binary and real Zenoh telemetry.  The local Fabric
proxy and recording facilitator are protocol doubles only; they make payment
verification outcomes observable without mocking the Tunnel or simulator.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
import uuid
import zenoh

from test_e2e_paid_action import (
    LocalFabricProxy,
    _FacilitatorHandler,
    _http_post,
    _payment_signature_from_402,
    _start_facilitator,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))
SKILL_CATALOG_PATH = os.path.join(
    _REPO_ROOT,
    "registry",
    "vendors",
    "pollen-robotics",
    "reachy-mini",
    "pollen-robotics.reachy-mini.mujoco-webots-sim.v1",
    "skill-catalog.json",
)

ACTION_TOPIC = "robot/tunnel/action"
METRICS_TOPIC = "robot/reachy_mini/metrics"
RESULT_TOPIC = "robot/tunnel/result"

# Locate real Go tunnel binary
candidates = [os.environ["TUNNEL_BIN"]] if os.environ.get("TUNNEL_BIN") else []
candidates += [
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
    _zenoh_peer = None

    @classmethod
    def setUpClass(cls):
        # 1. Ensure real Tunnel binary exists
        if not TUNNEL_BINARY or not os.path.isfile(TUNNEL_BINARY):
            raise unittest.SkipTest(
                "Real Go Tunnel binary not found. Build it with 'make build' in repo root "
                "or 'go build -o tunnel_bin ./cmd' inside tunnel/ folder."
            )

        # Own the Zenoh transport used by every process in this test.  The
        # peer is a real Zenoh listener, not a simulator double: using an
        # explicit connect-only config ensures subscribers observe exactly the
        # same action/result path as the Tunnel and bridge.
        cls._zenoh_endpoint = "tcp/127.0.0.1:7447"
        cls._zenoh_peer = zenoh.open(
            zenoh.Config.from_json5(
                '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
                '"listen":{"endpoints":["tcp/127.0.0.1:7447"]}}'
            )
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json5", prefix="reachy_payment_gate_zenoh_", delete=False
        ) as zenoh_file:
            json.dump(
                {
                    "mode": "peer",
                    "scouting": {"multicast": {"enabled": False}},
                    "connect": {"endpoints": [cls._zenoh_endpoint]},
                },
                zenoh_file,
            )
            cls._zenoh_cfg_path = zenoh_file.name

        # The production Tunnel is reached through the Fabric WebSocket proxy;
        # it does not expose a local HTTP listener. Reuse the protocol-accurate
        # local proxy/facilitator used by the positive E2E test.
        cls._proxy = LocalFabricProxy()
        cls._proxy.start()
        cls._facilitator, cls._facilitator_thread = _start_facilitator()

        # 2. Write real tunnel configuration
        cfg_path = os.path.abspath(os.path.join(_HERE, "_tmp_real_tunnel_cfg.json"))
        tunnel_cfg = {
            "robot_id": "reachy_mini",
            "price": "$0.001",
            "network": "eip155:84532",
            "evm_payee_address": "0x0000000000000000000000000000000000000001",
            "facilitator_url": f"http://127.0.0.1:{cls._facilitator.server_address[1]}",
            "proxy_ws_url": f"ws://127.0.0.1:{cls._proxy.port}/ws",
            "aip_enabled": False,
        }
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(tunnel_cfg, f)
        cls._cfg_path = cfg_path


        # 3. Launch real Go Tunnel binary
        zenoh_c_lib = os.path.normpath(os.path.join(_REPO_ROOT, ".zenoh-c", "lib"))
        env = os.environ.copy()
        env["PROXY_WS_URL"] = f"ws://127.0.0.1:{cls._proxy.port}/ws"
        env["FACILITATOR_URL"] = f"http://127.0.0.1:{cls._facilitator.server_address[1]}"
        env["AIP_ENABLED"] = "false"
        env["ZENOH_CONFIG"] = cls._zenoh_cfg_path
        env["SKILL_CATALOG_PATH"] = SKILL_CATALOG_PATH
        env["ALLOWED_ACTIONS"] = "look_at_apple,inspect_table,stop"
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


        if cls._proxy.wait_for_connection(15) is None:
            cls.tearDownClass()
            raise RuntimeError("Real Go Tunnel binary failed to connect to the Fabric WebSocket proxy.")





        # 4. Launch Reachy Mini simulator bridge (main.py)
        main_py = os.path.join(_HERE, "mujoco_sim_bridge", "main.py")
        bridge_env = env.copy()
        bridge_env["ROBOT_ID"] = "reachy_mini"
        cls._bridge_proc = subprocess.Popen(
            [sys.executable, main_py],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=bridge_env,
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
        if hasattr(cls, "_zenoh_cfg_path") and os.path.exists(cls._zenoh_cfg_path):
            os.unlink(cls._zenoh_cfg_path)
        if hasattr(cls, "_proxy"):
            cls._proxy.close()
        if hasattr(cls, "_facilitator"):
            cls._facilitator.shutdown()
            cls._facilitator.server_close()
            cls._facilitator_thread.join(timeout=5)
        if cls._zenoh_peer:
            cls._zenoh_peer.close()

    def test_1_unpaid_request_returns_http_402_payment_required(self):
        """Unpaid POST /action through real Tunnel must return HTTP 402 Payment Required."""
        action_events = []
        z_config = zenoh.Config.from_json5(
            '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
            f'"connect":{{"endpoints":["{self._zenoh_endpoint}"]}}}}'
        )
        z_session = zenoh.open(z_config)
        action_sub = z_session.declare_subscriber(
            ACTION_TOPIC,
            lambda sample: action_events.append(bytes(sample.payload.to_bytes())),
        )
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._proxy.port}/robots/reachy_mini/action",
            data=json.dumps({"action": "look_at_apple"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                body = json.loads(resp.read())
                response_headers = dict(resp.headers)
        except urllib.error.HTTPError as e:
            status = e.code
            raw_body = e.read()
            body = json.loads(raw_body) if raw_body else None
            response_headers = dict(e.headers)

        self.assertEqual(status, 402, f"Expected 402 Payment Required from real Tunnel, got {status}")
        self.assertTrue(
            "PAYMENT-REQUIRED" in {key.upper() for key in response_headers}
            or (body is not None and "payment_requirements" in body),
            "Expected x402 payment requirements in the 402 response",
        )
        time.sleep(1.0)
        action_sub.undeclare()
        z_session.close()
        self.assertEqual(
            action_events, [],
            "Invalid/unpaid payment must not publish robot/tunnel/action or move the simulator",
        )
        print("\n[Real Tunnel Test] Unpaid request correctly rejected with HTTP 402 Payment Required.")

    def test_2_malformed_json_without_payment_is_rejected_before_action(self):
        """An unpaid malformed request is rejected by x402 before action dispatch."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._proxy.port}/robots/reachy_mini/action",
            data=b'{"action": "look_at_apple"',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code

        self.assertEqual(status, 402, f"Expected payment gate to reject request, got {status}")
        print("[Real Tunnel Test] Malformed unpaid request correctly rejected with HTTP 402.")

    def test_3_facilitator_rejected_payment_never_crosses_action_boundary(self):
        """A paid-shaped signature rejected by /verify must fail closed at HTTP 402.

        This is intentionally a real Go Tunnel + x402 middleware integration
        test.  The local facilitator records every call and returns a valid
        HTTP response with ``isValid: false`` to model a tampered payment
        signature.  The assertions cover all authorization boundaries: no
        Tunnel ActionEvent, no correlated simulator output, and no settlement.
        """
        _FacilitatorHandler.calls = []
        _FacilitatorHandler.verify_response = {
            "isValid": False,
            "invalidReason": "test-tampered-payment",
        }
        action_events = []
        metrics = []
        results = []
        z_config = zenoh.Config.from_json5(
            '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
            f'"connect":{{"endpoints":["{self._zenoh_endpoint}"]}}}}'
        )
        z_session = zenoh.open(z_config)
        action_sub = z_session.declare_subscriber(
            ACTION_TOPIC,
            lambda sample: action_events.append(json.loads(bytes(sample.payload.to_bytes()))),
        )
        metrics_sub = z_session.declare_subscriber(
            METRICS_TOPIC,
            lambda sample: metrics.append(json.loads(bytes(sample.payload.to_bytes()))),
        )
        result_sub = z_session.declare_subscriber(
            RESULT_TOPIC,
            lambda sample: results.append(json.loads(bytes(sample.payload.to_bytes()))),
        )
        request_id = f"reachy-invalid-payment-{uuid.uuid4().hex}"

        try:
            public_url = f"http://127.0.0.1:{self._proxy.port}/robots/reachy_mini/action"
            unpaid_status, unpaid_headers, _ = _http_post(
                public_url, {"action": "look_at_apple"}
            )
            self.assertEqual(unpaid_status, 402)

            paid_status, _, paid_body = _http_post(
                public_url,
                {
                    "action": "look_at_apple",
                    "robot_id": "reachy_mini",
                    "action_id": request_id,
                    "idempotency_key": request_id,
                    "params": {"target_object": "apple", "duration": 2.0},
                },
                {"PAYMENT-SIGNATURE": _payment_signature_from_402(unpaid_headers)},
            )
            self.assertEqual(
                paid_status,
                402,
                f"facilitator-rejected payment must return HTTP 402, got {paid_status}: "
                f"{paid_body.decode('utf-8', errors='replace')}",
            )

            # Give the real Tunnel/Zenoh path a short observation window.  The
            # bridge can drive MuJoCo only from robot/tunnel/action, so zero
            # ActionEvents plus zero correlated bridge output is the observable
            # no-state-change proof without substituting a simulator mock.
            # A rejected verification must never reach PostAction.
            time.sleep(2.0)
            self.assertEqual(action_events, [], "invalid payment published an ActionEvent")
            self.assertEqual(
                [event for event in metrics if event.get("correlation_id") == request_id],
                [],
                "invalid payment changed simulator state / emitted metrics",
            )
            self.assertEqual(
                [event for event in results if event.get("action_id") == request_id],
                [],
                "invalid payment emitted a terminal simulator result",
            )

            verify_calls = [
                payload for path, payload in _FacilitatorHandler.calls if path == "/verify"
            ]
            settle_calls = [
                payload for path, payload in _FacilitatorHandler.calls if path == "/settle"
            ]
            self.assertEqual(len(verify_calls), 1, "payment must be verified exactly once")
            self.assertEqual(settle_calls, [], "invalid payment must never settle")
            print(
                "[Real Tunnel Test] Facilitator-rejected payment: HTTP 402, "
                "0 ActionEvents, 0 simulator outputs, 0 settlements."
            )
        finally:
            action_sub.undeclare()
            metrics_sub.undeclare()
            result_sub.undeclare()
            z_session.close()
            # Keep other integration tests independent when this module is
            # run as part of a larger unittest invocation.
            _FacilitatorHandler.verify_response = None


if __name__ == "__main__":
    unittest.main(verbosity=2)
