"""First-action M20 proof through real Tunnel, Zenoh, MuJoCo and x402.

The Fabric proxy and facilitator are controlled local protocol endpoints; they
only avoid a wallet/public-network dependency.  The Go Tunnel/x402 middleware,
WebSocket reader, Zenoh transport, M20 bridge, and vendor-MJCF MuJoCo episode
are the production implementations.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from m20_pro_mujoco_bridge.bridge import BridgeSettings, M20ZenohBridge
from m20_pro_mujoco_bridge.contracts import ROBOT_ID as REGISTERED_ROBOT_ID
from m20_pro_mujoco_bridge.model import resolve_model_dir
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
    / "registry/vendors/deep-robotics/lynx-m20-pro"
    / "deep-robotics.lynx-m20-pro.mujoco-webots-obstacle-nav.v1/skill-catalog.json"
)
ROBOT_ID = REGISTERED_ROBOT_ID
ZENOH_PORT = int(os.environ.get("M20_POSITIVE_E2E_ZENOH_PORT", "7459"))


class M20PositivePaidActionTests(unittest.TestCase):
    def test_first_paid_action_runs_vendor_mujoco_and_settles_once(self) -> None:
        """No warm-up: first paid action must produce one correlated success/settlement."""

        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Go Tunnel first with make build")
        try:
            resolve_model_dir()
        except FileNotFoundError as error:
            raise unittest.SkipTest(str(error))

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator(
            {"isValid": True, "payer": "0x1111111111111111111111111111111111111111"}
        )
        observer = bridge = tunnel = None
        try:
            observer = ZenohObserver(port=ZENOH_PORT)
            bridge = M20ZenohBridge(
                settings=BridgeSettings(
                    robot_id=ROBOT_ID,
                    zenoh_endpoint=f"tcp/127.0.0.1:{ZENOH_PORT}",
                    zenoh_config_path=None,
                    action_topic="robot/tunnel/action",
                    result_topic="robot/tunnel/result",
                    metrics_topic="robot/deep_robotics_m20/metrics",
                )
            )
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="m20_positive_e2e_") as temp_dir:
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
                        "ALLOWED_ACTIONS": "navigate_obstacle_course,stop",
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
                time.sleep(0.5)  # explicit local Zenoh peers discover each other before action one

                action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"
                unpaid_status, unpaid_headers, _ = http_post(action_url, {"action": "navigate_obstacle_course"})
                self.assertEqual(unpaid_status, 402)

                action_id = f"m20-paid-{uuid.uuid4().hex}"
                paid_status, _, paid_body = http_post(
                    action_url,
                    {
                        "action": "navigate_obstacle_course",
                        "robot_id": ROBOT_ID,
                        "action_id": action_id,
                        "idempotency_key": action_id,
                        "params": {"goalDistanceM": 1.35, "wheelSpeedRadS": 4.0, "maxDurationSec": 16},
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
                result = matching_results[0]["result"]
                self.assertEqual(matching_results[0]["status"], "success")
                self.assertTrue(result["success"])
                self.assertGreaterEqual(result["measured_forward_distance_m"], 1.35)
                self.assertGreaterEqual(result["min_base_height_m"], 0.45)
                self.assertTrue(result["course"]["obstacle_detected"])
                self.assertTrue(result["course"]["obstacle_released"])
                self.assertEqual(len([call for call in FacilitatorHandler.calls if call[0] == "/verify"]), 1)
                self.assertEqual(len([call for call in FacilitatorHandler.calls if call[0] == "/settle"]), 1)
                print("[M20 E2E] first paid action -> vendor MuJoCo obstacle yield/resume, correlated success, one settlement")
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
