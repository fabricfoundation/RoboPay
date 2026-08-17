"""Phase 2 transport tests (stdlib unittest, zero external deps).

Covers the payment -> transport -> execution -> result flow with the action
envelope on the official topics robot/tunnel/action and robot/tunnel/result.

  - LoopbackTransport: deterministic stand-in (runs on any platform, including
    Windows where zenoh has no wheels). Exercises the identical envelope +
    correlation contract the real Zenoh path uses.
  - ZenohTransport: real zenoh over TCP loopback. Skipped automatically when
    zenoh is unavailable (Windows); runs on Linux / CI.
"""
import threading
import time
import unittest

from flow.executor import SkillResult
from flow.zenoh_transport import (
    ACTION_TOPIC,
    RESULT_TOPIC,
    LoopbackTransport,
    RobotHandler,
    ZenohRobotNode,
    ZenohTransport,
    _HAS_ZENOH,
)


class FakeExecutor:
    """Mirrors the future MuJoCo executor's success/failure contract."""

    def execute(self, skill_id, params):
        if params.get("object") == "unreachable":
            return SkillResult(False, "unreachable")
        return SkillResult(True, "cube moved")


ACTION_OK = {
    "actionId": "a1",
    "robotId": "xarm-real-001",
    "skillId": "pick_object",
    "paramsHash": "h",
    "params": {"object": "box"},
}
ACTION_FAIL = {
    "actionId": "a2",
    "robotId": "xarm-real-001",
    "skillId": "pick_object",
    "paramsHash": "h",
    "params": {"object": "unreachable"},
}


class TestTopics(unittest.TestCase):
    def test_official_topic_names(self):
        self.assertEqual(ACTION_TOPIC, "robot/tunnel/action")
        self.assertEqual(RESULT_TOPIC, "robot/tunnel/result")


class TestLoopbackTransport(unittest.TestCase):
    def test_success_flows_through_transport(self):
        t = LoopbackTransport(FakeExecutor())
        res = t.send_action(dict(ACTION_OK))
        self.assertEqual(res["actionId"], "a1")
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["message"], "cube moved")

    def test_failure_flows_through_transport(self):
        t = LoopbackTransport(FakeExecutor())
        res = t.send_action(dict(ACTION_FAIL))
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["message"], "unreachable")

    def test_result_envelope_keeps_contract_fields(self):
        t = LoopbackTransport(FakeExecutor())
        res = t.send_action(dict(ACTION_OK))
        for field in ("actionId", "robotId", "skillId", "paramsHash",
                     "status", "message"):
            self.assertIn(field, res)

    def test_concurrent_actions_correlate(self):
        t = LoopbackTransport(FakeExecutor())
        r1 = t.send_action(dict(ACTION_OK, actionId="c1"))
        r2 = t.send_action(dict(ACTION_FAIL, actionId="c2"))
        self.assertEqual(r1["actionId"], "c1")
        self.assertEqual(r1["status"], "completed")
        self.assertEqual(r2["actionId"], "c2")
        self.assertEqual(r2["status"], "failed")

    def test_robot_handler_is_transport_agnostic(self):
        # Proves the same execution logic backs both media.
        h = RobotHandler(FakeExecutor())
        out = h.handle(dict(ACTION_OK))
        self.assertEqual(out["status"], "completed")


@unittest.skipUnless(_HAS_ZENOH, "zenoh not installed (Linux only)")
class TestZenohTransport(unittest.TestCase):
    ENDPOINT = "tcp/127.0.0.1:17449"

    def test_real_zenoh_roundtrip(self):
        node = ZenohRobotNode(FakeExecutor(), endpoint=self.ENDPOINT)
        stop = threading.Event()
        t = threading.Thread(target=node.serve, kwargs={"stop_event": stop},
                             daemon=True)
        t.start()
        time.sleep(1.0)  # robot listening
        client = ZenohTransport(endpoint=self.ENDPOINT, connect_timeout=2.0)
        try:
            res = client.send_action(dict(ACTION_OK))
            self.assertEqual(res["actionId"], "a1")
            self.assertEqual(res["status"], "completed")
        finally:
            client.close()
            stop.set()
            t.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
