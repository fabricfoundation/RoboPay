"""Real Tunnel proof that failed Spot actions never call the x402 settle endpoint."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path

import zenoh

from x402_harness import (
    FacilitatorHandler,
    LocalFabricProxy,
    NETWORK,
    PAYEE,
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
    / "registry/vendors/boston-dynamics/spot"
    / "boston-dynamics.spot.mujoco-webots-obstacle-course.v1/skill-catalog.json"
)
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
ROBOT_ID = "spot_mujoco_nosettle"
ZENOH_TEST_PORT = int(os.environ.get("SPOT_TEST_ZENOH_PORT", "7447"))


class InjectedSpotSimulator:
    """Zenoh simulator double that injects a correlated failure or timeout."""

    def __init__(self):
        config = zenoh.Config.from_json5(
            '{"mode":"peer","scouting":{"multicast":{"enabled":false}},'
            f'"listen":{{"endpoints":["tcp/127.0.0.1:{ZENOH_TEST_PORT}"]}}}}'
        )
        self.session = zenoh.open(config)
        self.mode = "fail"
        self.actions: list[dict] = []
        self.publisher = self.session.declare_publisher(RESULT_TOPIC)
        self.subscriber = self.session.declare_subscriber(ACTION_TOPIC, self.on_action)

    def on_action(self, sample) -> None:
        event = json.loads(bytes(sample.payload.to_bytes()))
        self.actions.append(event)
        if self.mode == "silent":
            return
        self.publisher.put(
            json.dumps(
                {
                    "action_id": event.get("action_id", ""),
                    "robot_id": event.get("robot_id", ""),
                    "skill_id": event.get("skill_id", ""),
                    "params_hash": event.get("params_hash", ""),
                    "idempotency_key": event.get("idempotency_key", ""),
                    "status": "failure",
                    "execution_status": "FAILED",
                    "result": {"error_code": "INJECTED_SPOT_FAILURE"},
                }
            ).encode()
        )

    def close(self) -> None:
        self.subscriber.undeclare()
        self.publisher.undeclare()
        self.session.close()


def settle_calls() -> list[dict]:
    return [payload for path, payload in FacilitatorHandler.calls if path == "/settle"]


class SpotNoSettlementTests(unittest.TestCase):
    def test_failure_timeout_and_replay_never_settle(self) -> None:
        tunnel_binary = find_tunnel_binary(ROOT)
        if not tunnel_binary:
            raise unittest.SkipTest("Build the real Tunnel first with make build")

        proxy = LocalFabricProxy()
        facilitator, facilitator_thread = start_facilitator()
        simulator = None
        tunnel = None
        try:
            proxy.start()
            with tempfile.TemporaryDirectory(prefix="spot_nosettle_") as temp_dir:
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
                zenoh_config_path = temp / "zenoh.json5"
                zenoh_config_path.write_text(
                    json.dumps(
                        {
                            "mode": "peer",
                            "scouting": {"multicast": {"enabled": False}},
                            "connect": {"endpoints": [f"tcp/127.0.0.1:{ZENOH_TEST_PORT}"]},
                        }
                    ),
                    encoding="utf-8",
                )
                store_path = temp / "idempotency.json"

                def start_tunnel() -> subprocess.Popen:
                    child_env = os.environ.copy()
                    child_env.update(
                        {
                            "PROXY_WS_URL": f"ws://127.0.0.1:{proxy.port}/ws",
                            "FACILITATOR_URL": f"http://127.0.0.1:{facilitator.server_address[1]}",
                            "AIP_ENABLED": "false",
                            "ZENOH_CONFIG": str(zenoh_config_path),
                            "SKILL_CATALOG_PATH": str(SKILL_CATALOG),
                            "ALLOWED_ACTIONS": "navigate_obstacle_course",
                            "EXECUTION_TIMEOUT_SECONDS": "3",
                            "IDEMPOTENCY_STORE_PATH": str(store_path),
                        }
                    )
                    process = subprocess.Popen(
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
                    return process

                simulator = InjectedSpotSimulator()
                tunnel = start_tunnel()
                time.sleep(1)
                action_url = f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action"

                def status_url(action_id: str) -> str:
                    return f"http://127.0.0.1:{proxy.port}/robots/{ROBOT_ID}/action/{action_id}/status"

                unpaid_status, unpaid_headers, _ = http_post(
                    action_url, {"action": "navigate_obstacle_course"}
                )
                self.assertEqual(unpaid_status, 402)

                failed_id = f"spot-nosettle-failure-{uuid.uuid4().hex}"
                failed_body = {
                    "action": "navigate_obstacle_course",
                    "robot_id": ROBOT_ID,
                    "action_id": failed_id,
                    "idempotency_key": failed_id,
                    "params": {
                        "maxDurationSec": 48,
                        "side": "left",
                    },
                }
                status, _, body = http_post(
                    action_url,
                    failed_body,
                    {"PAYMENT-SIGNATURE": payment_signature_from_402(unpaid_headers)},
                )
                self.assertEqual(status, 202)
                self.assertEqual(json.loads(body).get("action_id"), failed_id)
                failed_terminal = poll_action_status(status_url(failed_id), {"failed", "timeout"}, 30)
                self.assertEqual(failed_terminal.get("state"), "failed")
                self.assertEqual(failed_terminal.get("error_code"), "SIMULATOR_EXECUTION_FAILED")
                self.assertFalse(failed_terminal.get("settled"))
                self.assertEqual(len(settle_calls()), 0)
                self.assertEqual(len(simulator.actions), 1)

                status, _, body = http_post(
                    action_url,
                    failed_body,
                    {"PAYMENT-SIGNATURE": payment_signature_from_402(unpaid_headers)},
                )
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(body).get("error_code"), "REPLAY_DETECTED")
                self.assertEqual(len(simulator.actions), 1)
                self.assertEqual(len(settle_calls()), 0)

                simulator.mode = "silent"
                timeout_id = f"spot-nosettle-timeout-{uuid.uuid4().hex}"
                timeout_signature = payment_signature_from_402(unpaid_headers)
                status, _, _ = http_post(
                    action_url,
                    {
                        "action": "navigate_obstacle_course",
                        "robot_id": ROBOT_ID,
                        "action_id": timeout_id,
                        "idempotency_key": timeout_id,
                        "params": {
                            "maxDurationSec": 48,
                            "side": "left",
                        },
                    },
                    {"PAYMENT-SIGNATURE": timeout_signature},
                )
                self.assertEqual(status, 202)
                timeout_terminal = poll_action_status(status_url(timeout_id), {"failed", "timeout"}, 30)
                self.assertEqual(timeout_terminal.get("state"), "timeout")
                self.assertEqual(timeout_terminal.get("error_code"), "SIMULATOR_RESULT_TIMEOUT")
                self.assertFalse(timeout_terminal.get("settled"))
                self.assertEqual(len(simulator.actions), 2)
                self.assertEqual(len(settle_calls()), 0)

                payment_replay_id = f"spot-nosettle-payment-replay-{uuid.uuid4().hex}"
                status, _, body = http_post(
                    action_url,
                    {
                        "action": "navigate_obstacle_course",
                        "robot_id": ROBOT_ID,
                        "action_id": payment_replay_id,
                        "idempotency_key": payment_replay_id,
                        "params": {},
                    },
                    {"PAYMENT-SIGNATURE": timeout_signature},
                )
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(body).get("error_code"), "PAYMENT_REPLAY_DETECTED")
                self.assertEqual(len(simulator.actions), 2)
                self.assertEqual(len(settle_calls()), 0)

                stale_connection = proxy.wait_for_connection(1)
                tunnel.terminate()
                tunnel.wait(timeout=5)
                if stale_connection is not None:
                    proxy.detach(stale_connection)
                tunnel = start_tunnel()
                status, _, body = http_post(
                    action_url,
                    failed_body,
                    {"PAYMENT-SIGNATURE": payment_signature_from_402(unpaid_headers)},
                )
                self.assertEqual(status, 409)
                self.assertEqual(json.loads(body).get("error_code"), "REPLAY_DETECTED")
                persisted = poll_action_status(status_url(failed_id), {"failed", "timeout"}, 10)
                self.assertEqual(persisted.get("state"), "failed")
                self.assertFalse(persisted.get("settled"))
                self.assertEqual(len(simulator.actions), 2)
                self.assertEqual(len(settle_calls()), 0)
                print("[SPOT NO-SETTLE] failure, timeout and replay: settle_calls=0")
        finally:
            if simulator is not None:
                simulator.close()
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
