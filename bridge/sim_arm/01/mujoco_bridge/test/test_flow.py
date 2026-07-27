"""End-to-end pay-to-actuate flow tests (no ROS2 / no Zenoh router needed).

Demonstrates the payment-safety gate the reviewer asked for:
  accepted/pending -> actual simulation -> actionId-correlated terminal result
                   -> success-only settlement, with a real failure/no-settle case.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sim_arm_01.flow import (
    ActionEnvelope, PaymentGuard, RoboPayRelay, InProcBus, RobotNode, params_hash,
)


def _stack():
    bus = InProcBus()
    guard = PaymentGuard()
    RobotNode(bus)
    return RoboPayRelay(bus, guard)


def test_valid_paid_success_settles():
    relay = _stack()
    a = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [1.0, -0.5]},
        payment={"txHash": "0xVALID", "amountUSDC": "0.50"})
    ack = relay.submit(a)
    assert ack["status"] == "accepted"                 # accepted/pending first
    assert relay.is_settled(a.actionId) is False       # not settled yet
    result = relay.await_result(a.actionId, timeout=30)
    assert result is not None and result.status == "success"
    assert result.actionId == a.actionId               # correlated by actionId
    assert relay.is_settled(a.actionId) is True         # settles only after success


def test_unpaid_rejected_never_actuates():
    relay = _stack()
    a = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [1.0, -0.5]}, payment={})
    ack = relay.submit(a)
    assert ack["status"] == "rejected" and ack["httpStatus"] == 402
    assert relay.await_result(a.actionId, timeout=2) is None   # never published
    assert relay.is_settled(a.actionId) is False


def test_tampered_params_rejected():
    relay = _stack()
    a = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [1.0, -0.5]}, payment={"txHash": "0xVALID"})
    a.paramsHash = params_hash({"target_qpos": [9.9, 9.9]})   # claim != actual
    ack = relay.submit(a)
    assert ack["status"] == "rejected" and ack["httpStatus"] == 400


def test_duplicate_idempotency_key_rejected():
    relay = _stack()
    first = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [0.3, 0.3]},
        payment={"txHash": "0xVALID"}, idempotencyKey="dup-1")
    relay.submit(first)
    relay.await_result(first.actionId, timeout=30)
    replay = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [0.3, 0.3]},
        payment={"txHash": "0xVALID"}, idempotencyKey="dup-1")
    ack = relay.submit(replay)
    assert ack["status"] == "rejected" and ack["httpStatus"] == 409


def test_unreachable_target_fails_and_does_not_settle():
    relay = _stack()
    a = ActionEnvelope(
        robotId="sim-arm-01", skillId="move_to_pose",
        params={"target_qpos": [5.0, 5.0]},           # outside reachable range
        payment={"txHash": "0xVALID2", "amountUSDC": "0.50"})
    ack = relay.submit(a)
    assert ack["status"] == "accepted"
    result = relay.await_result(a.actionId, timeout=30)
    assert result is not None
    assert result.status == "error" and result.code == "ACTION_FAILED"
    assert result.metrics["success"] is False
    assert relay.is_settled(a.actionId) is False       # failure must NOT settle
