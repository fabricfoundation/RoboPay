"""Mandatory real-Tunnel regression for facilitator-rejected X30 payments."""

from __future__ import annotations

import json
import os
import socket
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

from x30_pro_mujoco_bridge.bridge import BridgeSettings, X30ZenohBridge
from x30_pro_mujoco_bridge.contracts import ROBOT_ID as REGISTERED_ROBOT_ID
from x402_harness import (
    FacilitatorHandler,
    LocalFabricProxy,
    NETWORK,
    PAYEE,
    ZenohObserver,
    find_tunnel_binary,
    http_post,
    payment_signature_from_402,
    start_facilitator,
)


SKILL_CATALOG = (
    ROOT
    / "registry/vendors/deep-robotics/x30-pro"
    / "deep-robotics.x30-pro.mujoco-webots-inspection.v1/skill-catalog.json"
)
ROBOT_ID = REGISTERED_ROBOT_ID
ZENOH_PORT = int(os.environ.get("X30_PAYMENT_GATE_ZENOH_PORT", "7457"))


def _frame(payload: bytes, opcode: int, final: bool) -> bytes:
    header = bytes([(0x80 if final else 0) | opcode])
    if len(payload) < 126:
        return header + bytes([len(payload)]) + payload
    return header + bytes([126]) + len(payload).to_bytes(2, "big") + payload


class X30PaymentGateTests(unittest.TestCase):
    def test_websocket_reader_reassembles_continuation_frames(self) -> None:
        """The Fabric E2E reader accepts a fragmented first Tunnel response."""

        from x402_harness import TunnelConnection

        reader, writer = socket.socketpair()
        try:
            writer.sendall(
                _frame(b'{"id":"x30-paid",', opcode=1, final=False)
                + _frame(b'"status":202}', opcode=0, final=True)
            )
            opcode, payload = TunnelConnection(reader)._read_message()
            self.assertEqual(opcode, 1)
            self.assertEqual(json.loads(payload), {"id": "x30-paid", "status": 202})
        finally:
            reader.close()
            writer.close()

    def test_is_valid_false_returns_402_before_action_or_simulator_boundary(self) -> None:
        """Reproduce the reviewer case with a real Go Tunnel and real Zenoh."""

        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Go Tunnel first with make build")
        executions = 0

        def runner(*_args, **_kwargs):
            nonlocal executions
            executions += 1
            return {"success": True}

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator(
            {"isValid": False, "invalidReason": "reviewer-tampered-payment"}
        )
        observer = bridge = tunnel = None
        try:
            observer = ZenohObserver(port=ZENOH_PORT)
            bridge = X30ZenohBridge(
                settings=BridgeSettings(
                    robot_id=ROBOT_ID,
                    zenoh_endpoint=f"tcp/127.0.0.1:{ZENOH_PORT}",
                    zenoh_config_path=None,
                    action_topic="robot/tunnel/action",
                    result_topic="robot/tunnel/result",
                    metrics_topic="robot/deep_robotics_x30/metrics",
                ),
                episode_runner=runner,
            )
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="x30_payment_gate_") as temp_dir:
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
                env = os.environ.copy()
                zenoh_library = ROOT / ".zenoh-c" / "lib"
                env.update(
                    {
                        "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws",
                        "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                        "AIP_ENABLED": "false",
                        "ZENOH_CONFIG": str(zenoh_config),
                        "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                        "ALLOWED_ACTIONS": "perform_inspection_gait,stop",
                    }
                )
                if zenoh_library.is_dir():
                    env["LD_LIBRARY_PATH"] = f"{zenoh_library}:{env.get('LD_LIBRARY_PATH', '')}"
                tunnel = subprocess.Popen(
                    [tunnel_binary, "--config", str(config_path)],
                    cwd=ROOT,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                )
                self.assertIsNotNone(proxy.wait_for_connection(15), "real Tunnel did not connect")
                action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"
                unpaid_status, unpaid_headers, _ = http_post(action_url, {"action": "perform_inspection_gait"})
                self.assertEqual(unpaid_status, 402)

                action_id = f"x30-tampered-{uuid.uuid4().hex}"
                status, _, body = http_post(
                    action_url,
                    {
                        "action": "perform_inspection_gait",
                        "robot_id": ROBOT_ID,
                        "action_id": action_id,
                        "idempotency_key": action_id,
                        "params": {},
                    },
                    {"PAYMENT-SIGNATURE": payment_signature_from_402(unpaid_headers)},
                )
                self.assertEqual(status, 402, body.decode("utf-8", errors="replace"))
                time.sleep(2.0)
                verify_calls = [item for item in FacilitatorHandler.calls if item[0] == "/verify"]
                settle_calls = [item for item in FacilitatorHandler.calls if item[0] == "/settle"]
                self.assertEqual(len(verify_calls), 1)
                self.assertEqual(settle_calls, [])
                self.assertEqual(executions, 0)
                self.assertEqual(
                    observer.snapshot(),
                    (0, 0, 0),
                    "invalid payment crossed ActionEvent, bridge output, or simulator metrics",
                )
                print("[X30 PAYMENT GATE] isValid:false -> 402, 0 actions, 0 simulator output, 0 settlements")
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
