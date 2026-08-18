from flow.relay import Relay
from flow.executor import MockExecutor
from flow.payment import PaymentState
import pytest

PAY = {"txHash": "0x" + "cd" * 32, "amount": "0.10",
       "network": "base-sepolia",
       "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
       "payer": "0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a"}


def _relay(fail_skill=None):
    return Relay(executor=MockExecutor(fail_skill=fail_skill))


def test_settles_only_on_success():
    r = _relay().handle({"skill": "move_forward", "payment": PAY,
                         "idempotencyKey": "k1"})
    assert r["status"] == "completed"
    assert r["settled"] is True
    assert r["paymentState"] == PaymentState.SUCCESS.value


def test_no_settle_on_failure():
    r = _relay(fail_skill="move_forward").handle(
        {"skill": "move_forward", "payment": PAY, "idempotencyKey": "k2"})
    assert r["status"] == "failed"
    assert r["settled"] is False
    assert r["paymentState"] == PaymentState.FAILED.value


def test_replay_key_rejected():
    rel = _relay()
    rel.handle({"skill": "move_forward", "payment": PAY, "idempotencyKey": "k3"})
    r2 = rel.handle({"skill": "move_forward", "payment": PAY, "idempotencyKey": "k3"})
    assert r2["status"] == "rejected"
    assert r2.get("settled") is not True
