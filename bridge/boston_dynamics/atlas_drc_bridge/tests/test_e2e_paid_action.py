"""Positive first-action proof through the real Tunnel, Zenoh bridge, and MuJoCo.

This test deliberately uses a local Fabric-protocol proxy and recording
facilitator so no wallet or public network is needed. Those are only protocol
doubles: the Go Tunnel/x402 middleware, Zenoh transport, Atlas bridge, and
real MuJoCo episode are the production implementations.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))

from atlas_drc_bridge.bridge import AtlasZenohBridge, BridgeSettings
from atlas_drc_bridge.model import resolve_model_dir
from x402_harness import (
    FacilitatorHandler,
    LocalFabricProxy,
    NETWORK,
    PAYEE,
    ZenohObserver,
    find_tunnel_binary,
    http_post,
    payment_signature_from_402,
    poll_action_status,
    start_facilitator,
)


SKILL_CATALOG = (
    ROOT
    / "registry/vendors/boston-dynamics/atlas"
    / "boston-dynamics.atlas-drc.mujoco-webots-wave.v1/skill-catalog.json"
)
ROBOT_ID = "atlas_drc_positive_e2e"
ZENOH_PORT = int(os.environ.get("ATLAS_POSITIVE_E2E_ZENOH_PORT", "7447"))


class AtlasPositivePaidActionTests(unittest.TestCase):
    def test_first_paid_action_runs_real_mujoco_then_paid_stop_settles(self) -> None:
        """No warm-up action: the paid wave and its separately paid stop settle."""

        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Go Tunnel first with make build")
        try:
            resolve_model_dir()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        observer = bridge = tunnel = None
        try:
            observer = ZenohObserver(port=ZENOH_PORT)
            bridge = AtlasZenohBridge(
                settings=BridgeSettings(
                    robot_id=ROBOT_ID,
                    zenoh_endpoint=f"tcp/127.0.0.1:{ZENOH_PORT}",
                    zenoh_config_path=None,
                    action_topic="robot/tunnel/action",
                    result_topic="robot/tunnel/result",
                    metrics_topic="robot/boston_dynamics_atlas_drc/metrics",
                )
            )
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="atlas_positive_e2e_") as temp_dir:
                temp = Path(temp_dir)
                config_path = temp / "tunnel.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "robot_id": ROBOT_ID,
                            "evm_payee_address": PAYEE,
                            "price": "$0.001",
                            "network": NETWORK,
                        }
                    ),
                    encoding="utf-8",
                )
                zenoh_config = temp / "zenoh.json5"
                zenoh_config.write_text(
                    json.dumps(
                        {
                            "mode": "peer",
                            "scouting": {"multicast": {"enabled": False}},
                            "connect": {"endpoints": [f"tcp/127.0.0.1:{ZENOH_PORT}"]},
                        }
                    ),
                    encoding="utf-8",
                )
                environment = os.environ.copy()
                environment.update(
                    {
                        "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws",
                        "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                        "AIP_ENABLED": "false",
                        "ZENOH_CONFIG": str(zenoh_config),
                        "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                        "ALLOWED_ACTIONS": "wave_right_arm,stop",
                        "EXECUTION_TIMEOUT_SECONDS": "15",
                    }
                )
                zenoh_library = ROOT / ".zenoh-c" / "lib"
                if zenoh_library.is_dir():
                    environment["LD_LIBRARY_PATH"] = (
                        f"{zenoh_library}:{environment.get('LD_LIBRARY_PATH', '')}"
                    )
                tunnel = subprocess.Popen(
                    [tunnel_binary, "--config", str(config_path)],
                    cwd=ROOT,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
                self.assertIsNotNone(proxy.wait_for_connection(15), "real Tunnel did not connect")
                time.sleep(0.5)  # allow explicit local Zenoh peers to discover one another

                action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"
                unpaid_status, unpaid_headers, _ = http_post(action_url, {"action": "wave_right_arm"})
                self.assertEqual(unpaid_status, 402)

                action_id = f"atlas-paid-{uuid.uuid4().hex}"
                paid_status, _, paid_body = http_post(
                    action_url,
                    {
                        "action": "wave_right_arm",
                        "robot_id": ROBOT_ID,
                        "action_id": action_id,
                        "idempotency_key": action_id,
                        "params": {"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 5},
                    },
                    {"PAYMENT-SIGNATURE": payment_signature_from_402(unpaid_headers)},
                )
                accepted = json.loads(paid_body)
                self.assertEqual(paid_status, 202, paid_body.decode("utf-8", errors="replace"))
                self.assertEqual(accepted.get("state"), "pending")
                self.assertEqual(accepted.get("action_id"), action_id)

                terminal = poll_action_status(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action/{action_id}/status",
                    {"succeeded", "failed", "timeout", "settlement_failed"},
                    timeout=30,
                )
                self.assertEqual(terminal["state"], "succeeded", terminal)
                self.assertTrue(terminal["settled"], terminal)
                self.assertTrue(terminal.get("settlement", {}).get("transaction"), terminal)

                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    _, results, metrics = observer.snapshot()
                    if results and metrics:
                        break
                    time.sleep(0.05)
                matching_actions = [event for event in observer.actions if event.get("action_id") == action_id]
                matching_results = [event for event in observer.results if event.get("action_id") == action_id]
                matching_metrics = [event for event in observer.metrics if event.get("action_id") == action_id]
                self.assertEqual(len(matching_actions), 1, observer.actions)
                self.assertEqual(len(matching_results), 1, observer.results)
                self.assertEqual(len(matching_metrics), 1, observer.metrics)
                self.assertEqual(matching_results[0]["status"], "success")
                self.assertTrue(matching_results[0]["result"]["success"])
                self.assertGreaterEqual(matching_results[0]["result"]["completed_half_waves"], 2)
                self.assertGreater(matching_results[0]["result"]["measured_wave_stroke_rad"], 0.40)
                self.assertEqual([call for call in FacilitatorHandler.calls if call[0] == "/verify"].__len__(), 1)
                self.assertEqual([call for call in FacilitatorHandler.calls if call[0] == "/settle"].__len__(), 1)

                stop_probe_status, stop_probe_headers, _ = http_post(action_url, {"action": "stop"})
                self.assertEqual(stop_probe_status, 402)
                stop_action_id = f"atlas-stop-{uuid.uuid4().hex}"
                stop_status, _, stop_body = http_post(
                    action_url,
                    {
                        "action": "stop",
                        "robot_id": ROBOT_ID,
                        "action_id": stop_action_id,
                        "idempotency_key": stop_action_id,
                        "params": {},
                    },
                    {"PAYMENT-SIGNATURE": payment_signature_from_402(stop_probe_headers)},
                )
                self.assertEqual(stop_status, 202, stop_body.decode("utf-8", errors="replace"))
                stop_terminal = poll_action_status(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action/{stop_action_id}/status",
                    {"succeeded", "failed", "timeout", "settlement_failed"},
                    timeout=15,
                )
                self.assertEqual(stop_terminal["state"], "succeeded", stop_terminal)
                self.assertTrue(stop_terminal["settled"], stop_terminal)
                stop_results = [
                    event for event in observer.results if event.get("action_id") == stop_action_id
                ]
                self.assertEqual(len(stop_results), 1, observer.results)
                self.assertEqual(stop_results[0]["status"], "success")
                self.assertTrue(stop_results[0]["result"]["safe_stop_applied"])
                self.assertEqual(
                    len([call for call in FacilitatorHandler.calls if call[0] == "/verify"]), 2
                )
                self.assertEqual(
                    len([call for call in FacilitatorHandler.calls if call[0] == "/settle"]), 2
                )
                print(
                    "[ATLAS E2E] first paid action -> MuJoCo success + settlement; "
                    "paid stop -> correlated safe-stop success + settlement"
                )
        finally:
            if tunnel is not None and tunnel.poll() is None:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
            if bridge is not None:
                bridge.close()
            if observer is not None:
                observer.close()
            proxy.close()
            facilitator.shutdown()
            facilitator.server_close()
            facilitator_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
