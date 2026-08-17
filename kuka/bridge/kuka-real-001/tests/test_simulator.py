"""D3 MuJoCo executor tests (headless, deterministic, CI-friendly).

Proves the skill is REAL physics (object moves, grasp attaches, force is
measured) and that the four required outcomes exist:
  success / unreachable / collision / timeout.
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
REQ = {"skill": "pick_object", "robotId": "kuka-real-001", "amount": "0.01"}


class TestMuJoCoPick(unittest.TestCase):

    def test_success_moves_object(self):
        r = MuJoCoSimulator().pick_object({"object": "cube"})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertEqual(m["graspState"], "attached")
        self.assertGreater(m["objectLifted"], 0.02)
        self.assertGreater(m["contactForce"], 0.0)
        self.assertIn("objectDelta", m)

    def test_failure_unreachable(self):
        r = MuJoCoSimulator().pick_object({"object": "unreachable"})
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "unreachable")

    def test_failure_collision(self):
        r = MuJoCoSimulator().pick_object({"object": "collision"})
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "collision")

    def test_failure_timeout(self):
        r = MuJoCoSimulator().pick_object({"object": "timeout"})
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "timeout")

    def test_relay_settles_only_on_success(self):
        ex = MuJoCoExecutor()
        r = Relay(ex)
        ok = r.handle({**REQ, "idempotencyKey": "sim-ok",
                       "payment": PAID, "params": {"object": "cube"}})
        self.assertEqual(ok["status"], "completed")
        self.assertTrue(ok["settled"])

        ex2 = MuJoCoExecutor()
        r2 = Relay(ex2)
        bad = r2.handle({**REQ, "idempotencyKey": "sim-bad",
                         "payment": PAID, "params": {"object": "unreachable"}})
        self.assertEqual(bad["status"], "failed")
        self.assertFalse(bad["settled"])     # NO settlement on failure


if __name__ == "__main__":
    unittest.main()
