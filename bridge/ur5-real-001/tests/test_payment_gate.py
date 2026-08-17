"""Exercise ur5-real-001's x402 payment gate through the real Go Tunnel binary.

Aligned with the winning RoboPay pattern: payment verification AND settlement
happen in the shared Go ``tunnel/`` binary (x402 ``X402Payment`` middleware +
x402 facilitator), never in Python. This test proves the fail-closed boundary:

  * an unpaid request is rejected with HTTP 402 and never reaches Zenoh;
  * a payment-shaped-but-invalid request (recording facilitator returns
    isValid:false) is also rejected with 402 and never reaches Zenoh;
  * in both cases the facilitator's /settle endpoint is never called.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from x402_harness import (
    ActionBoundaryObserver,
    FacilitatorHandler,
    LocalFabricProxy,
    NETWORK,
    PAYEE,
    find_tunnel_binary,
    http_post,
    payment_signature_from_402,
    start_facilitator,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]  # repository root (contains tunnel/ and bridge/)
ROBOT_ID = "ur5_arm_001_payment_gate"


class FabricPaymentGateTests(unittest.TestCase):
    def test_unpaid_and_invalid_payment_fail_closed(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        observer = None
        tunnel = None
        try:
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="ur5_payment_gate_") as temp_dir:
                temp = Path(temp_dir)
                config_path = temp / "tunnel.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "robot_id": ROBOT_ID,
                            "evm_payee_address": PAYEE,
                            "price": "$0.10",
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
                        # Fail-closed deployment allowlist + skill catalog: the
                        # real Tunnel refuses every paid action unless these are
                        # set (ALLOWLIST_NOT_CONFIGURED / SKILL_CATALOG_NOT_CONFIGURED).
                        "SKILL_CATALOG_PATH": str(PACKAGE_ROOT / "skill-catalog.json"),
                        "ALLOWED_ACTIONS": "pick_object",
                        "MAX_ACTION_DURATION_SECONDS": "60",
                        # Short window so the timeout no-settlement path is fast.
                        "EXECUTION_TIMEOUT_SECONDS": "5",
                        "IDEMPOTENCY_STORE_PATH": str(temp / "robopay_idempotency.json"),
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
                observer = ActionBoundaryObserver()

                # ---- unpaid ----
                unpaid_status, unpaid_headers, unpaid_body = http_post(
                    action_url, {"action": "pick_object"}
                )
                self.assertEqual(unpaid_status, 402)
                self.assertTrue(
                    "PAYMENT-REQUIRED" in {name.upper() for name in unpaid_headers},
                    "402 response must carry PAYMENT-REQUIRED",
                )
                # Prove the 402 challenge is wired for ur5-real-001.
                required = json.loads(
                    base64.b64decode(unpaid_headers.get("PAYMENT-REQUIRED"))
                )
                self.assertEqual(required.get("x402Version"), 2)
                self.assertEqual(required["accepts"][0]["network"], NETWORK)
                self.assertEqual(required["accepts"][0]["amount"], "0.10")
                self.assertEqual(
                    required["accepts"][0]["payTo"].lower(), PAYEE.lower()
                )

                # ---- malformed (params not an object) ----
                malformed_status, _, _ = http_post(
                    action_url, {"action": "pick_object", "params": "not-an-object"}
                )
                self.assertEqual(malformed_status, 402)
                self.assertEqual(
                    FacilitatorHandler.calls,
                    [],
                    "unpaid requests must not verify or settle a payment",
                )

                # ---- payment-shaped but invalid (recording facilitator rejects) ----
                FacilitatorHandler.verify_response = {
                    "isValid": False,
                    "invalidReason": "reviewer-tampered-payment",
                }
                tampered_id = f"ur5-tampered-payment-{uuid.uuid4().hex}"
                rejected_status, _, _ = http_post(
                    action_url,
                    {
                        "action": "pick_object",
                        "robot_id": ROBOT_ID,
                        "action_id": tampered_id,
                        "idempotency_key": tampered_id,
                        "params": {"object": "cube"},
                    },
                    {"PAYMENT-SIGNATURE": payment_signature_from_402(unpaid_headers)},
                )
                self.assertEqual(rejected_status, 402)
                verify_calls = [
                    path for path, _ in FacilitatorHandler.calls if path == "/verify"
                ]
                settle_calls = [
                    path for path, _ in FacilitatorHandler.calls if path == "/settle"
                ]
                self.assertEqual(len(verify_calls), 1)
                self.assertEqual(settle_calls, [])
                self.assertFalse(
                    observer.action_received.wait(2),
                    "an isValid:false payment must not publish an ActionEvent",
                )
                self.assertEqual(
                    observer.snapshot(),
                    (0, 0),
                    "payment rejection must emit zero ActionEvents and executable commands",
                )
                print("[FABRIC PAYMENT GATE] unpaid, malformed, isValid:false -> HTTP 402")
        finally:
            if observer is not None:
                observer.close()
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
