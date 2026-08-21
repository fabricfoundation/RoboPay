"""Real Tunnel proof: Atlas failure, timeout, and replay never settle x402."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

import zenoh

from x402_harness import (
    ACTION_TOPIC,
    FacilitatorHandler,
    LocalFabricProxy,
    NETWORK,
    PAYEE,
    RESULT_TOPIC,
    find_tunnel_binary,
    http_post,
    payment_signature_from_402,
    poll_action_status,
    start_facilitator,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
SKILL_CATALOG = (
    ROOT
    / "registry/vendors/boston-dynamics/atlas"
    / "boston-dynamics.atlas-drc.mujoco-webots-wave.v1/skill-catalog.json"
)
ROBOT_ID = "atlas_drc_no_settlement"
ZENOH_PORT = int(os.environ.get("ATLAS_NO_SETTLEMENT_ZENOH_PORT", "7447"))


class ControlledAtlasResultPeer:
    """Inject terminal failure/silence at the *real* Zenoh result boundary.

    This intentionally does not replace the Tunnel or x402 middleware. It is a
    deterministic simulator-fault injector used to prove settlement behavior
    for failure and timeout paths that should never reach a live facilitator.
    """

    def __init__(self):
        self.mode = "failure"
        self.actions: list[dict] = []
        self.lock = threading.Lock()
        self.session = zenoh.open(
            zenoh.Config.from_json5(
                '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
                f'"listen":{{"endpoints":["tcp/127.0.0.1:{ZENOH_PORT}"]}}}}'
            )
        )
        self.publisher = self.session.declare_publisher(RESULT_TOPIC)
        self.subscriber = self.session.declare_subscriber(ACTION_TOPIC, self._on_action)

    def _on_action(self, sample) -> None:
        event = json.loads(bytes(sample.payload.to_bytes()))
        with self.lock:
            self.actions.append(event)
            mode = self.mode
        if mode == "silent":
            return
        self.publisher.put(
            json.dumps(
                {
                    "action_id": event["action_id"],
                    "robot_id": event["robot_id"],
                    "skill_id": event["skill_id"],
                    "params_hash": event["params_hash"],
                    "idempotency_key": event["idempotency_key"],
                    "status": "failure",
                    "result": {"success": False, "error_code": "INJECTED_ATLAS_SIMULATOR_FAILURE"},
                }
            ).encode()
        )

    def action_count(self) -> int:
        with self.lock:
            return len(self.actions)

    def close(self) -> None:
        self.subscriber.undeclare()
        self.publisher.undeclare()
        self.session.close()


class AtlasNoSettlementTests(unittest.TestCase):
    def test_failure_timeout_payment_replay_and_restart_replay_never_settle(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Go Tunnel first with make build")
        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        simulator = tunnel = None
        try:
            proxy.start()
            simulator = ControlledAtlasResultPeer()
            with tempfile.TemporaryDirectory(prefix="atlas_no_settlement_") as temp_dir:
                temp = Path(temp_dir)
                tunnel_config = temp / "tunnel.json"
                tunnel_config.write_text(
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
                store = temp / "idempotency.json"

                def start_tunnel() -> subprocess.Popen:
                    env = os.environ.copy()
                    env.update(
                        {
                            "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws",
                            "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                            "AIP_ENABLED": "false",
                            "ZENOH_CONFIG": str(zenoh_config),
                            "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                            "ALLOWED_ACTIONS": "wave_right_arm,stop",
                            "EXECUTION_TIMEOUT_SECONDS": "3",
                            "IDEMPOTENCY_STORE_PATH": str(store),
                        }
                    )
                    zenoh_library = ROOT / ".zenoh-c" / "lib"
                    if zenoh_library.is_dir():
                        env["LD_LIBRARY_PATH"] = f"{zenoh_library}:{env.get('LD_LIBRARY_PATH', '')}"
                    process = subprocess.Popen(
                        [tunnel_binary, "--config", str(tunnel_config)],
                        cwd=ROOT,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.STDOUT,
                    )
                    self.assertIsNotNone(proxy.wait_for_connection(15), "real Tunnel did not connect")
                    return process

                tunnel = start_tunnel()
                action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"
                unpaid_status, unpaid_headers, _ = http_post(action_url, {"action": "wave_right_arm"})
                self.assertEqual(unpaid_status, 402)

                def request_body(action_id: str) -> dict:
                    return {
                        "action": "wave_right_arm",
                        "robot_id": ROBOT_ID,
                        "action_id": action_id,
                        "idempotency_key": action_id,
                        "params": {"cycles": 2, "amplitudeRad": 0.30, "maxDurationSec": 8},
                    }

                failed_id = f"atlas-failure-{uuid.uuid4().hex}"
                signature = payment_signature_from_402(unpaid_headers)
                status, _, _ = http_post(action_url, request_body(failed_id), {"PAYMENT-SIGNATURE": signature})
                self.assertEqual(status, 202)
                failed = poll_action_status(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action/{failed_id}/status",
                    {"failed", "timeout"},
                )
                self.assertEqual(failed["state"], "failed")
                self.assertFalse(failed["settled"])
                self.assertEqual([call for call in FacilitatorHandler.calls if call[0] == "/settle"], [])
                self.assertEqual(simulator.action_count(), 1)

                replay_status, _, replay_body = http_post(
                    action_url, request_body(failed_id), {"PAYMENT-SIGNATURE": signature}
                )
                self.assertEqual(replay_status, 409)
                self.assertEqual(json.loads(replay_body)["error_code"], "REPLAY_DETECTED")
                self.assertEqual(simulator.action_count(), 1)

                simulator.mode = "silent"
                timeout_id = f"atlas-timeout-{uuid.uuid4().hex}"
                timeout_signature = payment_signature_from_402(unpaid_headers)
                timeout_status, _, _ = http_post(
                    action_url,
                    request_body(timeout_id),
                    {"PAYMENT-SIGNATURE": timeout_signature},
                )
                self.assertEqual(timeout_status, 202)
                timed_out = poll_action_status(
                    f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action/{timeout_id}/status",
                    {"failed", "timeout"},
                )
                self.assertEqual(timed_out["state"], "timeout")
                self.assertFalse(timed_out["settled"])
                self.assertEqual(simulator.action_count(), 2)
                self.assertEqual([call for call in FacilitatorHandler.calls if call[0] == "/settle"], [])

                payment_replay_status, _, payment_replay_body = http_post(
                    action_url,
                    request_body(f"atlas-payment-replay-{uuid.uuid4().hex}"),
                    {"PAYMENT-SIGNATURE": timeout_signature},
                )
                self.assertEqual(payment_replay_status, 409)
                self.assertEqual(json.loads(payment_replay_body)["error_code"], "PAYMENT_REPLAY_DETECTED")
                self.assertEqual(simulator.action_count(), 2)

                stale_connection = proxy.wait_for_connection(1)
                tunnel.terminate()
                tunnel.wait(timeout=5)
                if stale_connection is not None:
                    proxy.detach(stale_connection)
                tunnel = start_tunnel()
                restart_status, _, restart_body = http_post(
                    action_url, request_body(failed_id), {"PAYMENT-SIGNATURE": signature}
                )
                self.assertEqual(restart_status, 409)
                self.assertEqual(json.loads(restart_body)["error_code"], "REPLAY_DETECTED")
                self.assertEqual(simulator.action_count(), 2)
                self.assertEqual([call for call in FacilitatorHandler.calls if call[0] == "/settle"], [])
                print("[ATLAS NO-SETTLE] failure, timeout, payment replay, restart replay: settle_calls=0")
        finally:
            if tunnel is not None and tunnel.poll() is None:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
            if simulator is not None:
                simulator.close()
            proxy.close()
            facilitator.shutdown()
            facilitator.server_close()
            facilitator_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
