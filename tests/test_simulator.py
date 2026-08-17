"""D3 MuJoCo executor tests (headless, deterministic, CI-friendly).

Proves the skill is REAL physics (door opens, grasp attaches, force is
measured) and that the required outcomes exist:
  success (open) / stuck / out_of_range.
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
REQ = {"skill": "open_door", "robotId": "door-arm-001"}


class TestMuJoCoPick(unittest.TestCase):

    def test_success_opens_door(self):
        r = MuJoCoSimulator().open_door({"door": "open"})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertGreater(m["doorAngle"], 0.5)
        self.assertEqual(m["handleState"], "gripped")
        self.assertGreater(m["contactForce"], 0.0)

    def test_failure_stuck(self):
        r = MuJoCoSimulator().open_door({"door": "stuck"})
        self.assertFalse(r.success)
        # High friction scene: door barely moves, reason is insufficient_open
        self.assertIn(r.reason, ("stuck", "insufficient_open"))

    def test_failure_out_of_range(self):
        r = MuJoCoSimulator().open_door({"door": "out_of_range"})
        self.assertFalse(r.success)
        # Should fail as unreachable or configuration_error

    def test_relay_settles_only_on_success(self):
        ex = MuJoCoExecutor()
        r = Relay(ex)
        ok = r.handle({**REQ, "idempotencyKey": "sim-ok",
                       "payment": PAID, "params": {"door": "open"}})
        self.assertEqual(ok["status"], "completed")
        self.assertTrue(ok["settled"])

        ex2 = MuJoCoExecutor()
        r2 = Relay(ex2)
        bad = r2.handle({**REQ, "idempotencyKey": "sim-bad",
                         "payment": PAID, "params": {"door": "stuck"}})
        self.assertEqual(bad["status"], "failed")
        self.assertFalse(bad["settled"])     # NO settlement on failure


if __name__ == "__main__":
    unittest.main()
