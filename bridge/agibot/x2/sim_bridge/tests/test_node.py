"""Tests for skill resolution, execution routing, and the settlement rule.

The runner is stubbed throughout: whether a failed action settles is a property
of the bridge, not of the simulator, and it should be testable without one.
"""

from __future__ import annotations

import pytest

from ..x2.action_contract import ActionEnvelope, ActionRejected, RejectionCode, canonical_params_hash
from ..x2.mapper import (
    GOAL_X,
    GOAL_Y,
    MAX_PUSH,
    MIN_PUSH,
    PUCK_X,
    PUCK_Y,
    SKILLS,
    TaskSpec,
    catalogue,
    resolve,
)
from ..x2.node import ActionNode, ExecutionResult, IdempotencyStore
from ..simulation.metrics import RunMetrics

ROBOT = "x2-sim-001"


def _mid(bound) -> float:
    return round((bound.low + bound.high) / 2.0, 4)


#: Derived from the advertised envelope rather than written out, so that
#: narrowing the envelope cannot leave these tests asserting against
#: coordinates the skill no longer accepts.
PARAMS = {
    "puck_x": _mid(PUCK_X),
    "puck_y": _mid(PUCK_Y),
    "goal_x": _mid(GOAL_X),
    "goal_y": _mid(GOAL_Y),
}


def envelope(skill="push_to_target", params=None, key="idem-1", paid=True, robot=ROBOT):
    params = PARAMS if params is None else params
    return ActionEnvelope.from_json({
        "actionId": f"act_{key}",
        "robotId": robot,
        "skillId": skill,
        "params": dict(params),
        "idempotencyKey": key,
        "paramsHash": canonical_params_hash(params),
        "payment": {
            "provider": "x402", "amount": "10000", "asset": "USDC",
            "network": "eip155:84532", "verified": paid,
            **({"txHash": "0x" + "cd" * 32} if paid else {}),
        },
    })


def ok_metrics(**kw) -> RunMetrics:
    return RunMetrics(engine="stub", success=True, displacement=0.15,
                      final_distance=0.03, tolerance=0.05, **kw)


def bad_metrics(reason="puck did not reach the goal") -> RunMetrics:
    return RunMetrics(engine="stub", success=False, reason=reason,
                      displacement=0.01, final_distance=0.18, tolerance=0.05)


def node(runner) -> ActionNode:
    return ActionNode(ROBOT, runner, IdempotencyStore())


# -- catalogue ------------------------------------------------------------


def test_catalogue_exposes_a_priced_discoverable_skill():
    entries = {entry["name"]: entry for entry in catalogue(ROBOT)}
    assert "push_to_target" in entries
    paid = entries["push_to_target"]
    assert paid["priceUSDC"] == "0.01"
    assert paid["paymentRequired"] is True
    assert paid["robotId"] == ROBOT
    assert set(paid["paramsSchema"]) == set(PARAMS)


def test_catalogue_includes_a_free_stop_skill():
    entries = {entry["name"]: entry for entry in catalogue(ROBOT)}
    assert entries["stop"]["paymentRequired"] is False


# -- parameter validation -------------------------------------------------


def test_resolves_valid_parameters():
    task = resolve(envelope())
    assert task.skill_id == "push_to_target"
    assert task.puck_xy == (PARAMS["puck_x"], PARAMS["puck_y"])
    assert task.goal_xy == (PARAMS["goal_x"], PARAMS["goal_y"])


def test_rejects_unknown_skill():
    with pytest.raises(ActionRejected) as exc:
        resolve(envelope(skill="fly"))
    assert exc.value.code == RejectionCode.UNKNOWN_SKILL


def test_rejects_target_outside_the_reachable_workspace():
    with pytest.raises(ActionRejected) as exc:
        resolve(envelope(params=dict(PARAMS, puck_y=0.90)))
    assert exc.value.code == RejectionCode.PARAMS_OUT_OF_RANGE


def test_rejects_missing_parameter():
    params = dict(PARAMS)
    params.pop("goal_x")
    with pytest.raises(ActionRejected) as exc:
        resolve(envelope(params=params))
    assert exc.value.code == RejectionCode.PARAMS_OUT_OF_RANGE


def test_rejects_non_numeric_parameter():
    with pytest.raises(ActionRejected) as exc:
        resolve(envelope(params=dict(PARAMS, goal_x="over there")))
    assert exc.value.code == RejectionCode.PARAMS_OUT_OF_RANGE


def test_rejects_a_push_that_is_too_short():
    # Both points must be inside their own ranges, or the range check fires
    # first and this stops testing the distance rule at all. The puck sits at
    # the top of its band and the goal at the bottom of its own, which is the
    # shortest push the envelope can express.
    params = {
        "puck_x": _mid(PUCK_X),
        "puck_y": PUCK_Y.high,
        "goal_x": _mid(PUCK_X),
        "goal_y": GOAL_Y.low,
    }
    assert abs(params["goal_y"] - params["puck_y"]) < MIN_PUSH
    with pytest.raises(ActionRejected) as exc:
        resolve(envelope(params=params))
    assert exc.value.code == RejectionCode.PARAMS_OUT_OF_RANGE
    assert "push distance" in str(exc.value)


def test_push_bounds_are_ordered():
    assert 0 < MIN_PUSH < MAX_PUSH


# -- execution and settlement ---------------------------------------------


def test_successful_action_settles():
    result = node(lambda task: ok_metrics()).handle(envelope())
    assert result.status == "success"
    assert result.settle is True
    assert result.metrics["displacementM"] == 0.15


def test_failed_action_does_not_settle():
    """The rule the whole integration exists to protect."""
    result = node(lambda task: bad_metrics()).handle(envelope())
    assert result.status == "error"
    assert result.settle is False
    assert result.error["code"] == "ACTION_FAILED"


def test_a_crashing_runner_does_not_settle():
    def explode(task):
        raise RuntimeError("simulator died")

    result = node(explode).handle(envelope())
    assert result.status == "error"
    assert result.settle is False
    assert "simulator died" in result.error["message"]


def test_unpaid_action_does_not_reach_the_runner():
    calls = []

    def runner(task):
        calls.append(task)
        return ok_metrics()

    result = node(runner).handle(envelope(paid=False))
    assert result.settle is False
    assert result.error["code"] == RejectionCode.PAYMENT_MISSING
    assert calls == [], "an unpaid action must not actuate the robot"


def test_envelope_for_another_robot_does_not_reach_the_runner():
    calls = []
    result = node(lambda t: calls.append(t) or ok_metrics()).handle(
        envelope(robot="another-robot")
    )
    assert result.settle is False
    assert result.error["code"] == RejectionCode.UNKNOWN_ROBOT
    assert calls == []


def test_deliberate_failure_skill_never_settles():
    result = node(lambda task: ok_metrics()).handle(
        envelope(skill="diagnostic_fail", params={})
    )
    assert result.status == "error"
    assert result.settle is False


def test_free_stop_skill_needs_no_payment():
    result = node(lambda task: ok_metrics()).handle(
        envelope(skill="stop", params={}, paid=False)
    )
    assert result.status == "success"


# -- idempotency ----------------------------------------------------------


def test_replay_does_not_execute_twice_and_does_not_settle():
    calls = []

    def runner(task):
        calls.append(task)
        return ok_metrics()

    bridge = node(runner)
    first = bridge.handle(envelope(key="same-key"))
    second = bridge.handle(envelope(key="same-key"))

    assert first.settle is True
    assert second.settle is False
    assert second.replayed is True
    assert second.error["code"] == RejectionCode.REPLAYED
    assert len(calls) == 1, "the robot must not move twice for one payment"


def test_distinct_keys_execute_independently():
    calls = []
    bridge = node(lambda t: calls.append(t) or ok_metrics())
    bridge.handle(envelope(key="key-a"))
    bridge.handle(envelope(key="key-b"))
    assert len(calls) == 2


# -- response shape -------------------------------------------------------


def test_success_response_matches_the_documented_shape():
    body = ExecutionResult.success("act_1", "push_to_target", "Action completed", {}).to_json()
    assert body["status"] == "success"
    assert body["skill"] == "push_to_target"
    assert body["result"]["message"] == "Action completed"
    assert body["settle"] is True


def test_failure_response_matches_the_documented_shape():
    body = ExecutionResult.failure(
        "act_1", "push_to_target", "ACTION_FAILED", "robot failed to complete action"
    ).to_json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "ACTION_FAILED"
    assert body["error"]["message"] == "robot failed to complete action"
    assert body["settle"] is False


def test_every_catalogued_skill_resolves_or_is_deliberately_unrunnable():
    for skill_id in SKILLS:
        params = PARAMS if skill_id == "push_to_target" else {}
        task = resolve(envelope(skill=skill_id, params=params))
        assert isinstance(task, TaskSpec)
