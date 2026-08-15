"""Integration-level tests for bridge/m20_pro_zenoh_bridge.py's
_on_action handler.

Payment verification/settlement now happens entirely in the Go tunnel
before an event ever reaches robot/tunnel/action (see
tunnel/internal/handlers: X402VerifyOnly + ExecutionWatcher). This
bridge trusts every event it receives already passed that gate; its
own job is limited to: parse correctly, reject the wrong skill or bad
params, refuse to dispatch a replayed actionId, and publish a
truthful terminal result correlated by actionId so the tunnel's
watcher can decide whether to settle.
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
sys.path.insert(0, os.path.join(THIS_DIR, ".."))

from bridge.m20_pro_zenoh_bridge import M20ProBridge, SKILL_ID  # noqa: E402


def make_event(action=SKILL_ID, action_id="act_test_1", **param_overrides):
    params = {"target_xy": [8.0, 0.0], "max_episode_steps": 50000}
    params.update(param_overrides)
    return {"actionId": action_id, "action": action, "params": params, "timestamp": "2026-01-01T00:00:00Z"}


def make_sample(event: dict):
    """Fakes a zenoh.Sample enough for _on_action: it only reads
    sample.payload and calls bytes() on it."""
    return SimpleNamespace(payload=json.dumps(event).encode("utf-8"))


@pytest.fixture
def bridge():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # __init__ constructs a real M20ProMuJoCoRunner (loads the MuJoCo
    # scene) -- cheap enough to do for real rather than mocking the
    # constructor, and it means these tests also catch a broken scene path.
    b = M20ProBridge(db_path=db_path)
    published = []
    b._publish = lambda result: published.append(result)
    b.published = published
    yield b
    b.guard.close()
    os.remove(db_path)


def test_valid_event_dispatches_and_publishes_success(bridge):
    event = make_event()
    fake_metrics = {
        "status": "goal_reached", "displacement_m": 7.65, "path_length_m": 7.66,
        "collisions": 0, "target_distance_remaining_m": 0.35,
        "sim_steps": 2935, "sim_seconds": 5.87,
    }
    with patch.object(bridge.runner, "run_episode", return_value=fake_metrics) as mock_run:
        bridge._on_action(make_sample(event))

    assert mock_run.called
    assert len(bridge.published) == 1
    result = bridge.published[0]
    assert result["actionId"] == "act_test_1"
    assert result["status"] == "success"
    assert result["simulatorStatus"] == "goal_reached"
    assert result["metrics"]["collisions"] == 0


def test_wrong_skill_rejected_without_dispatch(bridge):
    """An event for a skill this bridge doesn't serve must never reach
    the simulator."""
    event = make_event(action="some_other_skill")
    with patch.object(bridge.runner, "run_episode") as mock_run:
        bridge._on_action(make_sample(event))

    assert not mock_run.called
    assert len(bridge.published) == 1
    assert bridge.published[0]["status"] == "rejected"
    assert bridge.published[0]["errorCode"] == "unknown_skill"


def test_missing_params_rejected_without_dispatch(bridge):
    event = {"actionId": "act_test_2", "action": SKILL_ID, "params": {}, "timestamp": ""}
    with patch.object(bridge.runner, "run_episode") as mock_run:
        bridge._on_action(make_sample(event))

    assert not mock_run.called
    assert bridge.published[0]["status"] == "rejected"
    assert bridge.published[0]["errorCode"] == "invalid_params"


def test_malformed_json_is_dropped_silently_no_crash(bridge):
    """No actionId is parseable from malformed JSON, so nothing is
    published -- but the bridge must not crash."""
    sample = SimpleNamespace(payload=b"not valid json {{{")
    bridge._on_action(sample)  # must not raise
    assert len(bridge.published) == 0


def test_event_missing_action_id_is_dropped_silently_no_crash(bridge):
    """parse_action_event returns None for an event missing actionId --
    there is nothing to correlate a result against, so it is dropped,
    not defaulted to some placeholder id."""
    sample = SimpleNamespace(payload=json.dumps(
        {"action": SKILL_ID, "params": {"target_xy": [8.0, 0.0]}}
    ).encode())
    bridge._on_action(sample)
    assert len(bridge.published) == 0


def test_replayed_action_id_rejected_without_second_dispatch(bridge):
    """The exact scenario called out in review: replay must not cause a
    second simulator dispatch."""
    event = make_event()
    fake_metrics = {
        "status": "goal_reached", "displacement_m": 7.65, "path_length_m": 7.66,
        "collisions": 0, "target_distance_remaining_m": 0.35,
        "sim_steps": 2935, "sim_seconds": 5.87,
    }
    with patch.object(bridge.runner, "run_episode", return_value=fake_metrics) as mock_run:
        bridge._on_action(make_sample(event))  # first: executes
        bridge._on_action(make_sample(event))  # replay: must be rejected

    assert mock_run.call_count == 1, "replay must not trigger a second simulator dispatch"
    assert len(bridge.published) == 2
    assert bridge.published[0]["status"] == "success"
    assert bridge.published[1]["status"] == "rejected"
    assert bridge.published[1]["errorCode"] == "replay_detected"


def test_simulator_failure_yields_error_result(bridge):
    """If the simulator itself errors out, the published result must be
    status=error (never success), so the tunnel's execution watcher
    never settles this action."""
    event = make_event(action_id="act_test_fail")
    with patch.object(bridge.runner, "run_episode", side_effect=RuntimeError("boom")):
        bridge._on_action(make_sample(event))

    assert len(bridge.published) == 1
    result = bridge.published[0]
    assert result["status"] == "error"
    assert result["errorCode"] == "simulator_failure"


def test_simulator_timeout_yields_error_result(bridge):
    """Simulator ran (no exception) but did not reach the goal in time --
    result must be status=error even though dispatch itself didn't
    raise."""
    event = make_event(action_id="act_test_timeout")
    fake_metrics = {
        "status": "timeout", "displacement_m": 0.5, "path_length_m": 0.55,
        "collisions": 0, "target_distance_remaining_m": 7.6,
        "sim_steps": 500, "sim_seconds": 1.0,
    }
    with patch.object(bridge.runner, "run_episode", return_value=fake_metrics):
        bridge._on_action(make_sample(event))

    result = bridge.published[0]
    assert result["status"] == "error"
    assert result["simulatorStatus"] == "timeout"


def test_simulator_collision_yields_error_result(bridge):
    """Simulator reports a real collision -- result must be status=error
    even with status=goal_reached-adjacent metrics, because collisions>0
    must never settle."""
    event = make_event(action_id="act_test_collision")
    fake_metrics = {
        "status": "goal_reached", "displacement_m": 7.5, "path_length_m": 8.0,
        "collisions": 2, "target_distance_remaining_m": 0.3,
        "sim_steps": 3000, "sim_seconds": 6.0,
    }
    with patch.object(bridge.runner, "run_episode", return_value=fake_metrics):
        bridge._on_action(make_sample(event))

    result = bridge.published[0]
    assert result["status"] == "error"
    assert result["metrics"]["collisions"] == 2


def test_handle_stop_requires_no_payment_and_succeeds():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    bridge = M20ProBridge(db_path=db_path)
    with patch.object(bridge.runner, "stop") as mock_stop:
        result = bridge.handle_stop("stop-action-1")
    assert mock_stop.called
    assert result["status"] == "success"
    assert result["actionId"] == "stop-action-1"
    bridge.guard.close()
    os.remove(db_path)
