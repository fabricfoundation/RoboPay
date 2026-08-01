"""
Integration-level tests for bridge/booster_k1_zenoh_bridge.py's
_on_action handler -- the core RoboPay gate: validate -> replay-guard
-> dispatch -> publish terminal result.

Zenoh itself and the real MuJoCo simulator are mocked out so these
tests run fast and deterministically; the wiring to the real Zenoh
session and the real simulator is covered separately (manual E2E demo
in docs/README.md, since it requires a running Zenoh session and a
multi-second physics simulation).
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(THIS_DIR, "..", "bridge"))

from action_validator import canonical_params_hash  # noqa: E402
from booster_k1_zenoh_bridge import BoosterK1Bridge  # noqa: E402


def make_envelope(**overrides):
    params = {"goal_x": 5.0, "goal_y": 0.0, "max_time_sec": 60}
    envelope = {
        "actionId": "act_test_1",
        "robotId": "booster-k1-sim-01",
        "skillId": "k1_navigate_avoid_obstacles",
        "params": params,
        "paramsHash": canonical_params_hash(params),
        "idempotencyKey": "idem_test_1",
        "payment": {
            "provider": "x402",
            "authorizationId": "auth_test_1",
            "verified": True,
            "status": "authorized",
            "settled": False,
            "network": "eip155:84532",
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "amount": "1000",
            "payTo": "0xRobotPayeeAddress",
            "issuedAt": "2026-07-31T10:00:00Z",
            "expiresAt": "2099-01-01T00:00:00Z",  # far future, never expires in tests
        },
    }
    envelope.update(overrides)
    return envelope


def make_sample(envelope: dict):
    """Fakes a zenoh.Sample enough for _on_action: it only reads
    sample.payload and calls bytes() on it."""
    return SimpleNamespace(payload=json.dumps(envelope).encode("utf-8"))


@pytest.fixture
def bridge():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    b = BoosterK1Bridge(db_path=db_path)
    b.guard = b.guard  # already constructed in __init__ with db_path
    published = []
    b._publish = lambda result: published.append(result)
    b.published = published
    yield b
    b.guard.close()
    os.remove(db_path)


def test_valid_action_dispatches_and_publishes_success(bridge):
    envelope = make_envelope()
    fake_metrics = {
        "status": "success", "distance_to_goal_m": 0.29, "path_length_m": 5.4,
        "collision_count": 0, "sim_time_sec": 30.0,
    }
    with patch("booster_k1_zenoh_bridge.dispatch_to_simulator", return_value=fake_metrics) as mock_dispatch:
        bridge._on_action(make_sample(envelope))

    assert mock_dispatch.called
    assert len(bridge.published) == 1
    result = bridge.published[0]
    assert result["actionId"] == "act_test_1"
    assert result["status"] == "success"
    assert result["simulatorStatus"] == "success"
    assert result["metrics"]["collision_count"] == 0


def test_invalid_payment_rejected_without_dispatch(bridge):
    """An unpaid/invalid action must never reach the simulator."""
    envelope = make_envelope()
    envelope["payment"]["verified"] = False

    with patch("booster_k1_zenoh_bridge.dispatch_to_simulator") as mock_dispatch:
        bridge._on_action(make_sample(envelope))

    assert not mock_dispatch.called, "simulator must not be dispatched for an unverified payment"
    assert len(bridge.published) == 1
    result = bridge.published[0]
    assert result["status"] == "rejected"
    assert result["errorCode"] == "payment_not_verified"


def test_replayed_action_rejected_without_second_dispatch(bridge):
    """Sending the identical paid action twice must dispatch the
    simulator only once -- this is the exact scenario called out in
    review: replay must not cause a second action."""
    envelope = make_envelope()
    fake_metrics = {
        "status": "success", "distance_to_goal_m": 0.29, "path_length_m": 5.4,
        "collision_count": 0, "sim_time_sec": 30.0,
    }
    with patch("booster_k1_zenoh_bridge.dispatch_to_simulator", return_value=fake_metrics) as mock_dispatch:
        bridge._on_action(make_sample(envelope))   # first: executes
        bridge._on_action(make_sample(envelope))   # replay: must be rejected

    assert mock_dispatch.call_count == 1, "replay must not trigger a second simulator dispatch"
    assert len(bridge.published) == 2
    assert bridge.published[0]["status"] == "success"
    assert bridge.published[1]["status"] == "rejected"
    assert bridge.published[1]["errorCode"] == "replay_detected"


def test_simulator_failure_does_not_settle(bridge):
    """If the simulator itself errors out, the published result must
    be status=error (never success), so a downstream settlement
    service governed by payment-policy.yaml's noSettleResultStatuses
    never settles this action."""
    envelope = make_envelope()
    with patch("booster_k1_zenoh_bridge.dispatch_to_simulator", side_effect=RuntimeError("boom")):
        bridge._on_action(make_sample(envelope))

    assert len(bridge.published) == 1
    result = bridge.published[0]
    assert result["status"] == "error"
    assert result["errorCode"] == "simulator_failure"


def test_simulator_reporting_failure_status_yields_error_result(bridge):
    """Simulator ran successfully (no exception) but the policy failed
    the task (e.g. collision or timeout) -- result must be status=error,
    not success, even though dispatch itself didn't raise."""
    envelope = make_envelope()
    fake_metrics = {
        "status": "collision_detected", "distance_to_goal_m": 3.5,
        "path_length_m": 2.1, "collision_count": 1, "sim_time_sec": 12.0,
    }
    with patch("booster_k1_zenoh_bridge.dispatch_to_simulator", return_value=fake_metrics):
        bridge._on_action(make_sample(envelope))

    result = bridge.published[0]
    assert result["status"] == "error"
    assert result["simulatorStatus"] == "collision_detected"


def test_malformed_json_is_dropped_silently_no_crash(bridge):
    """No actionId is available to correlate a result for malformed
    JSON, so nothing is published -- but the bridge must not crash."""
    sample = SimpleNamespace(payload=b"not valid json {{{")
    bridge._on_action(sample)  # must not raise
    assert len(bridge.published) == 0
