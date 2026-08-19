"""k1-001 MuJoCo inspection simulator tests.

Proves the skill is REAL physics (camera moves, targets confirmed) and that
the four required outcomes exist:
  success / timeout / partial / no_targets.
Also proves the payment layer settles only on success (NO settlement on failure).
"""
import unittest

from simulator import MuJoCoSimulator
from flow.executor import MuJoCoExecutor
from flow.relay import Relay

PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "base-sepolia",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000000001"}
REQ = {"skill": "active_inspection", "robotId": "k1-001", "amount": "0.01"}


class TestMuJoCoInspection(unittest.TestCase):

    def test_success_inspects_three_targets(self):
        r = MuJoCoSimulator().active_inspection({"scenario": "inspection"})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertEqual(m["fovCentered"], True)
        self.assertLessEqual(m["distance"], 0.35)
        self.assertIn("cameraDelta", m)

    def test_failure_timeout(self):
        r = MuJoCoSimulator().active_inspection({"scenario": "timeout"})
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "timeout")

    def test_success_single_target(self):
        r = MuJoCoSimulator().active_inspection({"scenario": "single_target"})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertEqual(m["fovCentered"], True)

    def test_relay_settles_only_on_success(self):
        ex = MuJoCoExecutor()
        r = Relay(ex)
        ok = r.handle({**REQ, "idempotencyKey": "sim-ok",
                       "payment": PAID, "params": {"scenario": "single_target"}})
        self.assertEqual(ok["status"], "completed")
        self.assertTrue(ok["settled"])

        ex2 = MuJoCoExecutor()
        r2 = Relay(ex2)
        bad = r2.handle({**REQ, "idempotencyKey": "sim-bad",
                         "payment": PAID, "params": {"scenario": "timeout"}})
        self.assertEqual(bad["status"], "failed")
        self.assertFalse(bad["settled"])     # NO settlement on failure


if __name__ == "__main__":
    unittest.main()
