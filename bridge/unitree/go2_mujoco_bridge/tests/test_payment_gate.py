"""Exercise Go2's x402 payment gate through the real Go Tunnel binary."""

from __future__ import annotations

import json
import os
import socket
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
    _TunnelConnection,
    find_tunnel_binary,
    http_get,
    http_post,
    payment_signature_from_402,
    start_facilitator,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
SKILL_CATALOG = (
    ROOT
    / "registry/vendors/unitree/go2"
    / "unitree.go2.mujoco-webots-obstacle-nav.v1/skill-catalog.json"
)
ROBOT_ID = "go2_mujoco_payment_gate"
ZENOH_TEST_PORT = int(os.environ.get("GO2_PAYMENT_GATE_ZENOH_PORT", "7447"))


def _server_frame(payload: bytes, opcode: int, final: bool) -> bytes:
    header = bytes([(0x80 if final else 0) | opcode])
    length = len(payload)
    if length < 126:
        return header + bytes([length]) + payload
    if length <= 0xFFFF:
        return header + bytes([126]) + length.to_bytes(2, "big") + payload
    return header + bytes([127]) + length.to_bytes(8, "big") + payload


class Go2PaymentGateTests(unittest.TestCase):
    def test_websocket_reader_reassembles_continuation_frames(self) -> None:
        reader, writer = socket.socketpair()
        try:
            writer.sendall(
                _server_frame(b'{"id":"paid-1",', opcode=1, final=False)
                + _server_frame(b'"status":202}', opcode=0, final=True)
            )
            opcode, payload = _TunnelConnection(reader)._read_message()
            self.assertEqual(opcode, 1)
            self.assertEqual(json.loads(payload), {"id": "paid-1", "status": 202})
        finally:
            reader.close()
            writer.close()

    def test_unpaid_malformed_and_facilitator_rejected_requests_fail_closed(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        observer = None
        tunnel = None
        try:
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="go2_payment_gate_") as temp_dir:
                observer = ActionBoundaryObserver(port=ZENOH_TEST_PORT)
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
                zenoh_config_path = Path(temp_dir) / "zenoh.json5"
                zenoh_config_path.write_text(
                    json.dumps(
                        {
                            "mode": "peer",
                            "scouting": {"multicast": {"enabled": False}},
                            "connect": {
                                "endpoints": [f"tcp/127.0.0.1:{ZENOH_TEST_PORT}"]
                            },
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
                        "ZENOH_CONFIG": str(zenoh_config_path),
                        "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                        "ALLOWED_ACTIONS": "navigate_obstacles,stop",
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
                    {"navigate_obstacles", "stop"},
                )
                self.assertTrue(all(item["price_usdc"] == "0.001" for item in discovered["skills"]))

                unpaid_status, unpaid_headers, _ = http_post(
                    action_url, {"action": "navigate_obstacles"}
                )
                self.assertEqual(unpaid_status, 402)
                self.assertTrue(
                    "PAYMENT-REQUIRED" in {name.upper() for name in unpaid_headers},
                    "402 response must carry PAYMENT-REQUIRED",
                )

                malformed_status, _, _ = http_post(
                    action_url,
                    {"action": "navigate_obstacles", "params": "not-an-object"},
                )
                self.assertEqual(malformed_status, 402)
                self.assertEqual(
                    FacilitatorHandler.calls,
                    [],
                    "unpaid requests must not verify or settle a payment",
                )

                # This is deliberately payment-shaped (it is built from the
                # Tunnel's real 402 requirements), but the recording
                # facilitator rejects its signature with HTTP 200/isValid:false.
                # It must never reach PostAction or cross the Zenoh action boundary.
                FacilitatorHandler.verify_response = {
                    "isValid": False,
                    "invalidReason": "reviewer-tampered-payment",
                }
                tampered_id = f"go2-tampered-payment-{uuid.uuid4().hex}"
                rejected_status, _, _ = http_post(
                    action_url,
                    {
                        "action": "navigate_obstacles",
                        "robot_id": ROBOT_ID,
                        "action_id": tampered_id,
                        "idempotency_key": tampered_id,
                        "params": {"maxDurationSec": 48, "side": "left"},
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
                print("[GO2 DISCOVERY] robot + skills + price: OK")
                print(
                    "[GO2 PAYMENT GATE] unpaid, malformed, and isValid:false actions: HTTP 402"
                )
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
