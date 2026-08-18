from flow.relay import Relay
from flow.executor import MockExecutor
import pytest

PAY = {"txHash": "0x" + "ef" * 32, "amount": "0.10",
       "network": "base-sepolia",
       "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
       "payer": "0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a"}


def test_no_payment_returns_402():
    r = Relay(executor=MockExecutor()).handle({"skill": "move_forward"})
    assert r.get("status") == 402 or r.get("paymentRequired") is True


def test_invalid_skill_rejected_not_settled():
    r = Relay(executor=MockExecutor()).handle(
        {"skill": "nonexistent_skill", "payment": PAY, "idempotencyKey": "x1"})
    assert r["status"] in ("rejected", "failed")
    assert r["settled"] is False
