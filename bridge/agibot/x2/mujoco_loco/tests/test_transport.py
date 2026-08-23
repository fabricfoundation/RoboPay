from flow.zenoh_transport import LoopbackTransport
from flow.executor import MockExecutor
import pytest


def test_loopback_roundtrip():
    t = LoopbackTransport(MockExecutor())
    res = t.send_action({"actionId": "a1", "robotId": "agibot-x2",
                         "skillId": "move_forward", "params": {}})
    assert res["status"] == "completed"
    assert res["actionId"] == "a1"
