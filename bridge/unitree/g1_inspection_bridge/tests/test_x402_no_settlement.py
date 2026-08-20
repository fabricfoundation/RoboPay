"""Failure, timeout, and replay proof through the real Go Tunnel."""

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

import zenoh


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from x402_harness import (  # noqa: E402
    FacilitatorHandler, LocalFabricProxy, NETWORK, PAYEE, find_tunnel_binary,
    http_post, payment_signature_from_402, poll_action_status, start_facilitator,
)


SKILL_CATALOG = ROOT / "registry/vendors/unitree/g1/unitree.g1.mujoco-webots-active-inspection.v1/skill-catalog.json"
ROBOT_ID = "unitree_g1_no_settlement"
PORT = int(os.environ.get("UNITREE_G1_NO_SETTLE_ZENOH_PORT", "7468"))


class InjectedSimulator:
    def __init__(self):
        config = zenoh.Config.from_json5('{"mode":"peer","scouting":{"multicast":{"enabled":false}},' + f'"listen":{{"endpoints":["tcp/127.0.0.1:{PORT}"]}}}}')
        self.session = zenoh.open(config); self.mode = "fail"; self.actions = []
        self.publisher = self.session.declare_publisher("robot/tunnel/result")
        self.subscriber = self.session.declare_subscriber("robot/tunnel/action", self.on_action)

    def on_action(self, sample):
        event = json.loads(bytes(sample.payload.to_bytes())); self.actions.append(event)
        if self.mode == "silent": return
        self.publisher.put(json.dumps({
            "action_id": event["action_id"], "robot_id": event["robot_id"], "skill_id": event["skill_id"],
            "params_hash": event["params_hash"], "idempotency_key": event["idempotency_key"],
            "status": "failure", "result": {"error_code": "INJECTED_G1_FAILURE"},
        }).encode())

    def close(self):
        self.subscriber.undeclare(); self.publisher.undeclare(); self.session.close()


def settlements(): return [payload for path, payload in FacilitatorHandler.calls if path == "/settle"]


class G1NoSettlementTests(unittest.TestCase):
    def test_failure_timeout_and_replay_never_settle(self):
        binary = find_tunnel_binary(ROOT)
        if not binary: raise unittest.SkipTest("build the real Tunnel with make build")
        proxy = LocalFabricProxy(); facilitator, thread = start_facilitator(); simulator = tunnel = None
        try:
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="g1_nosettle_") as temporary:
                temp = Path(temporary); config = temp / "tunnel.json"; zconfig = temp / "zenoh.json5"; store = temp / "idempotency.json"
                config.write_text(json.dumps({"robot_id": ROBOT_ID, "evm_payee_address": PAYEE, "price": "$0.001", "network": NETWORK}), encoding="utf-8")
                zconfig.write_text(json.dumps({"mode": "peer", "scouting": {"multicast": {"enabled": False}}, "connect": {"endpoints": [f"tcp/127.0.0.1:{PORT}"]}}), encoding="utf-8")
                env = os.environ.copy(); env.update({
                    "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws", "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                    "AIP_ENABLED": "false", "ZENOH_CONFIG": str(zconfig), "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                    "ALLOWED_ACTIONS": "inspect_target_sequence", "EXECUTION_TIMEOUT_SECONDS": "3", "IDEMPOTENCY_STORE_PATH": str(store),
                })
                simulator = InjectedSimulator()
                tunnel = subprocess.Popen([binary, "--config", str(config)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                self.assertIsNotNone(proxy.wait_for_connection(15)); time.sleep(1)
                action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"
                unpaid, headers, _ = http_post(action_url, {"action": "inspect_target_sequence"}); self.assertEqual(unpaid, 402)
                def status_url(aid): return action_url + f"/{aid}/status"
                failed_id = f"g1-failure-{uuid.uuid4().hex}"
                body = {"action": "inspect_target_sequence", "robot_id": ROBOT_ID, "action_id": failed_id, "idempotency_key": failed_id, "params": {"targets": ["left", "center", "right"]}}
                signature = payment_signature_from_402(headers)
                status, _, _ = http_post(action_url, body, {"PAYMENT-SIGNATURE": signature}); self.assertEqual(status, 202)
                terminal = poll_action_status(status_url(failed_id), {"failed", "timeout"}, 30)
                self.assertEqual(terminal["state"], "failed"); self.assertFalse(terminal.get("settled")); self.assertEqual(settlements(), [])
                replay, _, replay_body = http_post(action_url, body, {"PAYMENT-SIGNATURE": signature})
                self.assertEqual(replay, 409); self.assertEqual(json.loads(replay_body)["error_code"], "REPLAY_DETECTED"); self.assertEqual(len(simulator.actions), 1)
                simulator.mode = "silent"; timeout_id = f"g1-timeout-{uuid.uuid4().hex}"; timeout_signature = payment_signature_from_402(headers)
                status, _, _ = http_post(action_url, {"action": "inspect_target_sequence", "robot_id": ROBOT_ID, "action_id": timeout_id, "idempotency_key": timeout_id, "params": {}}, {"PAYMENT-SIGNATURE": timeout_signature})
                self.assertEqual(status, 202); terminal = poll_action_status(status_url(timeout_id), {"failed", "timeout"}, 30)
                self.assertEqual(terminal["state"], "timeout"); self.assertFalse(terminal.get("settled")); self.assertEqual(settlements(), [])
                payment_replay_id = f"g1-payment-replay-{uuid.uuid4().hex}"
                status, _, response = http_post(action_url, {"action": "inspect_target_sequence", "robot_id": ROBOT_ID, "action_id": payment_replay_id, "idempotency_key": payment_replay_id, "params": {}}, {"PAYMENT-SIGNATURE": timeout_signature})
                self.assertEqual(status, 409); self.assertEqual(json.loads(response)["error_code"], "PAYMENT_REPLAY_DETECTED"); self.assertEqual(settlements(), [])

                stale = proxy.wait_for_connection(1)
                tunnel.terminate(); tunnel.wait(5)
                if stale is not None:
                    proxy.detach(stale)
                tunnel = subprocess.Popen([binary, "--config", str(config)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
                self.assertIsNotNone(proxy.wait_for_connection(15))
                status, _, response = http_post(action_url, body, {"PAYMENT-SIGNATURE": payment_signature_from_402(headers)})
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(response)["error_code"], "REPLAY_DETECTED")
                persisted = poll_action_status(status_url(failed_id), {"failed", "timeout"}, 10)
                self.assertEqual(persisted["state"], "failed")
                self.assertFalse(persisted.get("settled"))
                self.assertEqual(len(simulator.actions), 2)
                self.assertEqual(settlements(), [])
        finally:
            if simulator: simulator.close()
            if tunnel and tunnel.poll() is None: tunnel.terminate(); tunnel.wait(5)
            proxy.close(); facilitator.shutdown(); facilitator.server_close(); thread.join(5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
