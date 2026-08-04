"""Exercise Spot's x402 payment gate through the real Go Tunnel binary."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from x402_harness import (
    FacilitatorHandler,
    LocalFabricProxy,
    NETWORK,
    PAYEE,
    find_tunnel_binary,
    http_get,
    http_post,
    start_facilitator,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
SKILL_CATALOG = (
    ROOT
    / "registry/vendors/boston-dynamics/spot"
    / "boston-dynamics.spot.mujoco-webots-obstacle-course.v1/skill-catalog.json"
)
ROBOT_ID = "spot_mujoco_payment_gate"


class SpotPaymentGateTests(unittest.TestCase):
    def test_unpaid_and_malformed_requests_are_rejected_before_execution(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        tunnel = None
        try:
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="spot_payment_gate_") as temp_dir:
                config_path = Path(temp_dir) / "tunnel.json"
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
                child_env = os.environ.copy()
                child_env.update(
                    {
                        "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws",
                        "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                        "AIP_ENABLED": "false",
                        "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                        "ALLOWED_ACTIONS": "navigate_obstacle_course,stop",
                    }
                )
                tunnel = subprocess.Popen(
                    [tunnel_binary, "--config", str(config_path)],
                    cwd=ROOT,
                    env=child_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
                self.assertIsNotNone(
                    proxy.wait_for_connection(15),
                    "real Tunnel did not connect to the local Fabric proxy",
                )
                action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"

                robot_status, _, robot_body = http_get(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}"
                )
                self.assertEqual(robot_status, 200)
                self.assertEqual(json.loads(robot_body)["robot_id"], ROBOT_ID)
                skills_status, _, skills_body = http_get(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/skills"
                )
                self.assertEqual(skills_status, 200)
                discovered = json.loads(skills_body)
                self.assertEqual(
                    {item["skill_id"] for item in discovered["skills"]},
                    {"navigate_obstacle_course", "stop"},
                )
                self.assertTrue(all(item["price_usdc"] == "0.001" for item in discovered["skills"]))

                unpaid_status, unpaid_headers, _ = http_post(
                    action_url, {"action": "navigate_obstacle_course"}
                )
                self.assertEqual(unpaid_status, 402)
                self.assertTrue(
                    "PAYMENT-REQUIRED" in {name.upper() for name in unpaid_headers},
                    "402 response must carry PAYMENT-REQUIRED",
                )

                malformed_status, _, _ = http_post(
                    action_url,
                    {"action": "navigate_obstacle_course", "params": "not-an-object"},
                )
                self.assertEqual(malformed_status, 402)
                self.assertEqual(
                    FacilitatorHandler.calls,
                    [],
                    "unpaid requests must not verify or settle a payment",
                )
                print("[SPOT DISCOVERY] robot + skills + price: OK")
                print("[SPOT PAYMENT GATE] unpaid and malformed actions: HTTP 402")
        finally:
            if tunnel is not None and tunnel.poll() is None:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
            proxy.close()
            facilitator.shutdown()
            facilitator.server_close()
            facilitator_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
