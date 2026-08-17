"""D1 acceptance tests (stdlib unittest, zero external deps).

Covers the four required cases:
  - unpaid request rejected (no execution)
  - paid request executes and settles
  - duplicate idempotencyKey rejected (no double execution / no double settle)
  - execution failure does NOT settle
"""
import unittest

from flow.relay import Relay
from flow.executor import MockExecutor

PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "base-sepolia",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000000001"}
REQ = {"skill": "pick_object", "robotId": "ur5-real-001", "amount": "0.01"}


class TestPaymentFlow(unittest.TestCase):

    def test_unpaid_rejected(self):
        ex = MockExecutor()
        r = Relay(ex)
        resp = r.handle({**REQ, "idempotencyKey": "k1"})
        self.assertEqual(resp["status"], 402)
        self.assertTrue(resp["paymentRequired"])
        self.assertEqual(ex.execution_count, 0)

    def test_paid_executes_and_settles(self):
        ex = MockExecutor()
        r = Relay(ex)
        resp = r.handle({**REQ, "idempotencyKey": "k2", "payment": PAID})
        self.assertEqual(resp["status"], "completed")
        self.assertTrue(resp["settled"])
        self.assertEqual(ex.execution_count, 1)

    def test_duplicate_idempotency_rejected(self):
        ex = MockExecutor()
        r = Relay(ex)
        r.handle({**REQ, "idempotencyKey": "k3", "payment": PAID})
        resp2 = r.handle({**REQ, "idempotencyKey": "k3", "payment": PAID})
        self.assertEqual(resp2["status"], "rejected")
        self.assertEqual(resp2["reason"], "duplicate_idempotency_key")
        self.assertEqual(ex.execution_count, 1)        # not executed twice
        self.assertEqual(len(r.ledger.settled), 1)     # not settled twice

    def test_failure_no_settle(self):
        ex = MockExecutor(fail_skill="pick_object")
        r = Relay(ex)
        resp = r.handle({**REQ, "idempotencyKey": "k4", "payment": PAID})
        self.assertEqual(resp["status"], "failed")
        self.assertFalse(resp["settled"])              # NO settlement on failure
        self.assertEqual(ex.execution_count, 1)


if __name__ == "__main__":
    unittest.main()
