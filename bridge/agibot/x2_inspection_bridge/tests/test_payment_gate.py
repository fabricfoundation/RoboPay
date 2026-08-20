"""Prove rejected x402 evidence cannot cross the real Tunnel action boundary."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from x402_harness import (  # noqa: E402
    ActionBoundaryObserver, FacilitatorHandler, LocalFabricProxy, NETWORK, PAYEE,
    _TunnelConnection, find_tunnel_binary, http_get, http_post,
    payment_signature_from_402, start_facilitator,
)


SKILL_CATALOG = ROOT / "registry/vendors/agibot/x2/agibot.x2-ultra.mujoco-webots-active-inspection.v1/skill-catalog.json"
ROBOT_ID = "agibot_x2_payment_gate"
ZENOH_PORT = int(os.environ.get("AGIBOT_X2_PAYMENT_GATE_ZENOH_PORT", "7467"))


def _frame(payload: bytes, opcode: int, final: bool) -> bytes:
    header = bytes([(0x80 if final else 0) | opcode])
    if len(payload) < 126:
        return header + bytes([len(payload)]) + payload
    return header + bytes([126]) + len(payload).to_bytes(2, "big") + payload


class X2PaymentGateTests(unittest.TestCase):
    def test_positive_reader_assembles_continuation_frames(self):
        reader, writer = socket.socketpair()
        try:
            writer.sendall(_frame(b'{"id":"cold-start",', 1, False) + _frame(b'"status":202}', 0, True))
            opcode, payload = _TunnelConnection(reader)._read_message()
            self.assertEqual(opcode, 1); self.assertEqual(json.loads(payload)["status"], 202)
        finally:
            reader.close(); writer.close()

    def test_unpaid_malformed_missing_verdict_and_isvalid_false_fail_closed(self):
        binary = find_tunnel_binary(ROOT)
        if not binary:
            raise unittest.SkipTest("build the real Tunnel with make build")
        proxy = LocalFabricProxy(); facilitator, thread = start_facilitator(); observer = tunnel = None
        try:
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="x2_payment_gate_") as temporary:
                temp = Path(temporary); observer = ActionBoundaryObserver(port=ZENOH_PORT)
                config = temp / "tunnel.json"
                config.write_text(json.dumps({"robot_id": ROBOT_ID, "evm_payee_address": PAYEE, "price": "$0.001", "network": NETWORK}), encoding="utf-8")
                zenoh = temp / "zenoh.json5"
                zenoh.write_text(json.dumps({"mode": "peer", "scouting": {"multicast": {"enabled": False}}, "connect": {"endpoints": [f"tcp/127.0.0.1:{ZENOH_PORT}"]}}), encoding="utf-8")
                env = os.environ.copy(); env.update({
                    "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws", "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                    "AIP_ENABLED": "false", "ZENOH_CONFIG": str(zenoh), "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                    "ALLOWED_ACTIONS": "inspect_target_sequence,stop",
                })
                tunnel = subprocess.Popen([binary, "--config", str(config)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                self.assertIsNotNone(proxy.wait_for_connection(15))
                base = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}"
                self.assertEqual(http_get(base)[0], 200)
                status, _, body = http_get(base + "/skills"); self.assertEqual(status, 200)
                self.assertEqual({item["skill_id"] for item in json.loads(body)["skills"]}, {"inspect_target_sequence", "stop"})
                action_url = base + "/action"
                unpaid, headers, _ = http_post(action_url, {"action": "inspect_target_sequence"})
                self.assertEqual(unpaid, 402)
                malformed, _, _ = http_post(action_url, {"action": "inspect_target_sequence", "params": "bad"})
                self.assertEqual(malformed, 402); self.assertEqual(FacilitatorHandler.calls, [])
                FacilitatorHandler.verify_response = {"isValid": False, "invalidReason": "reviewer-tampered-payment"}
                action_id = f"x2-invalid-{uuid.uuid4().hex}"
                rejected, _, _ = http_post(action_url, {
                    "action": "inspect_target_sequence", "robot_id": ROBOT_ID, "action_id": action_id,
                    "idempotency_key": action_id, "params": {"maxDurationSec": 18, "targets": ["left", "center", "right"]},
                }, {"PAYMENT-SIGNATURE": payment_signature_from_402(headers)})
                self.assertEqual(rejected, 402)
                FacilitatorHandler.verify_response = {}
                missing_verdict_id = f"x2-missing-verdict-{uuid.uuid4().hex}"
                missing_verdict, _, _ = http_post(action_url, {
                    "action": "inspect_target_sequence", "robot_id": ROBOT_ID, "action_id": missing_verdict_id,
                    "idempotency_key": missing_verdict_id, "params": {"targets": ["center"]},
                }, {"PAYMENT-SIGNATURE": payment_signature_from_402(headers)})
                self.assertEqual(missing_verdict, 402)
                self.assertEqual(len([1 for path, _ in FacilitatorHandler.calls if path == "/verify"]), 2)
                self.assertEqual([1 for path, _ in FacilitatorHandler.calls if path == "/settle"], [])
                self.assertFalse(observer.action_received.wait(2))
                self.assertEqual(observer.snapshot(), (0, 0), "zero ActionEvents and zero simulator commands")
        finally:
            if observer: observer.close()
            if tunnel and tunnel.poll() is None: tunnel.terminate(); tunnel.wait(5)
            proxy.close(); facilitator.shutdown(); facilitator.server_close(); thread.join(5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
