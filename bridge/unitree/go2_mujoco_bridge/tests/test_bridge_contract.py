import json
import hashlib
import os
import socket
import threading
import time
import unittest
from unittest.mock import patch

import zenoh

from go2_mujoco_bridge.bridge import (
    ACTION_TOPIC,
    METRICS_TOPIC,
    READY_TOPIC,
    RESULT_TOPIC,
    BridgeSettings,
    Go2ZenohBridge,
    _load_event_parser,
)


class _Payload:
    def __init__(self, value: bytes):
        self._value = value

    def to_bytes(self) -> bytes:
        return self._value


class _Sample:
    def __init__(self, document: dict):
        self.payload = _Payload(json.dumps(document).encode("utf-8"))


class _Publisher:
    def __init__(self):
        self.documents: list[dict] = []

    def put(self, payload: bytes) -> None:
        self.documents.append(json.loads(payload))


def _event(action: str, params: dict | None = None, action_id: str = "action-1") -> dict:
    action_params = params or {}
    # Matches the Go Tunnel's encoding/json(map) representation for these
    # profile fixtures and lets the shared parser verify the paid param hash.
    canonical_params = json.dumps(
        action_params, separators=(",", ":"), sort_keys=True
    )
    return {
        "payload": {"skillId": action, "params": action_params},
        "action_id": action_id,
        "robot_id": "go2-contract-test",
        "skill_id": action,
        "idempotency_key": action_id,
        "params_hash": "sha256:" + hashlib.sha256(canonical_params.encode()).hexdigest(),
        "params_canonical": canonical_params,
        "transaction_details": {"payment_requirements": {"network": "eip155:84532"}},
    }


def _bridge() -> Go2ZenohBridge:
    bridge = Go2ZenohBridge.__new__(Go2ZenohBridge)
    bridge.robot_id = "go2-contract-test"
    bridge._model_dir = None
    bridge._parse_action_event = _load_event_parser()
    bridge._result_publisher = _Publisher()
    bridge._metrics_publisher = _Publisher()
    bridge._stop_event = threading.Event()
    bridge._stop_applied_event = threading.Event()
    bridge._worker_lock = threading.Lock()
    bridge._worker = None
    return bridge


class Go2BridgeContractTests(unittest.TestCase):
    def test_bridge_announces_ready_after_action_subscription_exists(self):
        """The cold-start E2E runner must not need a warm-up action or sleep."""

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        endpoint = f"tcp/127.0.0.1:{port}"
        router = zenoh.open(
            zenoh.Config.from_json5(
                json.dumps(
                    {
                        "mode": "peer",
                        "scouting": {"multicast": {"enabled": False}},
                        "listen": {"endpoints": [endpoint]},
                    }
                )
            )
        )
        ready = threading.Event()
        subscriber = router.declare_subscriber(READY_TOPIC, lambda _sample: ready.set())
        bridge = None
        try:
            bridge = Go2ZenohBridge(
                settings=BridgeSettings(
                    robot_id="go2-ready-contract",
                    zenoh_endpoint=endpoint,
                    zenoh_config_path=None,
                    action_topic=ACTION_TOPIC,
                    result_topic=RESULT_TOPIC,
                    metrics_topic=METRICS_TOPIC,
                    ready_topic=READY_TOPIC,
                )
            )
            self.assertTrue(ready.wait(timeout=5), "bridge did not announce its action subscription")
        finally:
            if bridge is not None:
                bridge.close()
            subscriber.undeclare()
            router.close()

    def test_parser_accepts_profile_skill_id_and_preserves_metadata(self):
        parser = _load_event_parser()
        parsed = parser(json.dumps(_event("navigate_obstacles")).encode("utf-8"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.action, "navigate_obstacles")
        self.assertEqual(parsed.action_id, "action-1")
        self.assertEqual(parsed.idempotency_key, "action-1")
        self.assertEqual(
            parsed.transaction_details["payment_requirements"]["network"],
            "eip155:84532",
        )

    def test_parser_rejects_missing_skill_and_invalid_params(self):
        parser = _load_event_parser()
        self.assertIsNone(parser(b'{"payload":{"params":{}}}'))
        self.assertIsNone(
            parser(b'{"payload":{"action":"navigate_obstacles","params":[]}}')
        )
        missing_correlation = _event("navigate_obstacles")
        del missing_correlation["idempotency_key"]
        self.assertIsNone(parser(json.dumps(missing_correlation).encode("utf-8")))

        tampered = _event("navigate_obstacles", {"side": "left"})
        tampered["payload"]["params"]["side"] = "right"
        self.assertIsNone(parser(json.dumps(tampered).encode("utf-8")))

    def test_success_and_failure_results_are_correlated(self):
        for simulator_result, expected_status in (
            ({"success": True, "completion_reason": "goal_reached"}, "success"),
            ({"success": False, "completion_reason": "time_limit"}, "failure"),
        ):
            with self.subTest(expected_status=expected_status):
                bridge = _bridge()
                with patch.dict(os.environ, {}, clear=True), patch(
                    "go2_mujoco_bridge.bridge.run_obstacle_nav",
                    return_value=simulator_result,
                ):
                    bridge._on_action(_Sample(_event("navigate_obstacles")))
                    bridge._worker.join(timeout=2)
                result = bridge._result_publisher.documents[-1]
                self.assertEqual(result["action_id"], "action-1")
                self.assertEqual(result["robot_id"], "go2-contract-test")
                self.assertEqual(result["skill_id"], "navigate_obstacles")
                self.assertEqual(result["idempotency_key"], "action-1")
                self.assertEqual(result["status"], expected_status)

    def test_unknown_and_invalid_actions_fail_without_simulation(self):
        cases = (
            (_event("sprint_through_wall"), "UNREGISTERED_ACTION"),
            (_event("navigate_obstacles", {"maxDurationSec": 61}), "INVALID_DURATION"),
            (_event("navigate_obstacles", {"speedScale": 1.5}), "INVALID_SPEED"),
            (_event("navigate_obstacles", {"side": "right"}), "INVALID_SIDE"),
            (_event("navigate_obstacles", {"request_id": "legacy"}), "INVALID_PARAMS"),
            (_event("stop", {"unexpected": True}), "INVALID_PARAMS"),
        )
        for document, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                bridge = _bridge()
                with patch("go2_mujoco_bridge.bridge.run_obstacle_nav") as runner:
                    bridge._on_action(_Sample(document))
                runner.assert_not_called()
                result = bridge._result_publisher.documents[-1]
                self.assertEqual(result["status"], "failure")
                self.assertEqual(result["result"]["error_code"], expected_code)

    def test_wrong_robot_is_dropped_without_simulator_or_foreign_result(self):
        bridge = _bridge()
        document = _event("navigate_obstacles")
        document["robot_id"] = "other-robot"
        with patch("go2_mujoco_bridge.bridge.run_obstacle_nav") as runner:
            bridge._on_action(_Sample(document))
        runner.assert_not_called()
        self.assertEqual(bridge._result_publisher.documents, [])

    def test_stop_interrupts_navigation_and_publishes_both_terminal_results(self):
        bridge = _bridge()
        started = threading.Event()

        def interrupted_runner(*_args, stop_requested, **_kwargs):
            started.set()
            deadline = time.monotonic() + 2
            while not stop_requested() and time.monotonic() < deadline:
                time.sleep(0.005)
            return {
                "success": False,
                "completion_reason": "safe_stopped",
                "safe_stop_applied": stop_requested(),
            }

        with patch.dict(os.environ, {}, clear=True), patch(
            "go2_mujoco_bridge.bridge.run_obstacle_nav",
            side_effect=interrupted_runner,
        ):
            bridge._on_action(_Sample(_event("navigate_obstacles", action_id="navigate-1")))
            self.assertTrue(started.wait(timeout=1))
            bridge._on_action(_Sample(_event("stop", action_id="stop-1")))
            bridge._worker.join(timeout=2)

        by_action = {
            item["action_id"]: item for item in bridge._result_publisher.documents
        }
        self.assertEqual(by_action["stop-1"]["status"], "success")
        self.assertTrue(by_action["stop-1"]["result"]["safe_stop_applied"])
        self.assertTrue(by_action["stop-1"]["result"]["active_execution_interrupted"])
        self.assertEqual(by_action["navigate-1"]["status"], "failure")
        self.assertTrue(by_action["navigate-1"]["result"]["safe_stop_applied"])


if __name__ == "__main__":
    unittest.main()
