from __future__ import annotations

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from x30_pro_mujoco_bridge.bridge import BridgeSettings, X30ZenohBridge
from x30_pro_mujoco_bridge.contracts import DRIVE_SKILL, ROBOT_ID


class _Publisher:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def put(self, payload: bytes) -> None:
        self.messages.append(json.loads(payload))

    def undeclare(self) -> None:
        pass


class _Session:
    def __init__(self) -> None:
        self.publishers: list[_Publisher] = []

    def declare_publisher(self, _topic: str) -> _Publisher:
        publisher = _Publisher()
        self.publishers.append(publisher)
        return publisher

    def declare_subscriber(self, _topic: str, _callback):
        return SimpleNamespace(undeclare=lambda: None)

    def close(self) -> None:
        pass


class X30BridgeContractTests(unittest.TestCase):
    def _bridge(self, runner):
        session = _Session()
        settings = BridgeSettings(
            robot_id=ROBOT_ID,
            zenoh_endpoint="tcp/127.0.0.1:7447",
            zenoh_config_path=None,
            action_topic="robot/tunnel/action",
            result_topic="robot/tunnel/result",
            metrics_topic="robot/deep_robotics_x30/metrics",
        )
        with patch("x30_pro_mujoco_bridge.bridge._open_zenoh_session", return_value=session):
            bridge = X30ZenohBridge(settings=settings, episode_runner=runner)
        return bridge, session

    def test_bridge_refuses_an_unregistered_robot_identity(self) -> None:
        settings = BridgeSettings(
            robot_id="other-x30",
            zenoh_endpoint="tcp/127.0.0.1:7447",
            zenoh_config_path=None,
            action_topic="robot/tunnel/action",
            result_topic="robot/tunnel/result",
            metrics_topic="robot/deep_robotics_x30/metrics",
        )
        with self.assertRaisesRegex(RuntimeError, "profile-scoped"):
            X30ZenohBridge(settings=settings, episode_runner=lambda *_args, **_kwargs: {})

    @staticmethod
    def _event(action: str, skill_id: str | None = None, params: dict | None = None):
        return SimpleNamespace(
            action_id="x30-contract-test",
            robot_id=ROBOT_ID,
            action=action,
            skill_id=skill_id or action,
            params=params if params is not None else {},
            params_hash="params-hash",
            idempotency_key="idem-x30-contract-test",
        )

    def test_invalid_unknown_and_wrong_robot_never_run_simulator(self) -> None:
        executions = 0

        def runner(*_args, **_kwargs):
            nonlocal executions
            executions += 1
            return {"success": True}

        bridge, session = self._bridge(runner)
        try:
            bridge._parse_action_event = lambda _payload: self._event("unknown")
            bridge._on_action(SimpleNamespace(payload=SimpleNamespace(to_bytes=lambda: b"ignored")))
            bridge._parse_action_event = lambda _payload: SimpleNamespace(
                **{**self._event(DRIVE_SKILL, params={}).__dict__, "robot_id": "other"}
            )
            bridge._on_action(SimpleNamespace(payload=SimpleNamespace(to_bytes=lambda: b"ignored")))
            self.assertEqual(executions, 0)
            self.assertEqual(len(session.publishers[0].messages), 2)
            self.assertTrue(all(item["status"] == "failure" for item in session.publishers[0].messages))
        finally:
            bridge.close()

    def test_valid_action_preserves_correlation_and_publishes_runner_result(self) -> None:
        finished = threading.Event()

        def runner(request, **_kwargs):
            self.assertEqual(request.skill_id, DRIVE_SKILL)
            finished.set()
            return {"success": True, "measured_lane_progress_m": 0.20}

        bridge, session = self._bridge(runner)
        try:
            event = self._event(DRIVE_SKILL, params={})
            bridge._parse_action_event = lambda _payload: event
            bridge._on_action(SimpleNamespace(payload=SimpleNamespace(to_bytes=lambda: b"ignored")))
            self.assertTrue(finished.wait(2))
            self.assertEqual(len(session.publishers[0].messages), 1)
            result = session.publishers[0].messages[0]
            self.assertEqual(result["action_id"], event.action_id)
            self.assertEqual(result["params_hash"], event.params_hash)
            self.assertEqual(result["status"], "success")
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
