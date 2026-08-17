"""D3 MuJoCo executor tests (headless, deterministic, CI-friendly).

Proves the skill is REAL physics (torso travels a genuine distance, the carried
object is acquired at the pickup zone and deposited at the drop zone, the budget
can genuinely exhaust) and that the two required outcomes exist:

  success  -- the drop zone is reached within the step budget (having passed the
              pickup zone)
  timeout  -- the step budget runs out before the drop zone (a real physics
              outcome, never a scripted success)

Also proves the payment layer settles only on success (NO settlement on timeout).
"""
import unittest

from simulator import MuJoCoSimulator
from flow.executor import MuJoCoExecutor
from flow.relay import Relay

HAS_SIM = True  # MuJoCo is a hard dependency of this backend

REQ = {"skill": "pick_and_carry", "robotId": "unitree-g1", "amount": "0.01"}
PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000000001"}


class TestMuJoCoWalk(unittest.TestCase):

    def test_pick_and_carry_succeeds_and_carries(self):
        r = MuJoCoSimulator().pick_and_carry({})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["reached"])
        self.assertTrue(m["carried"])
        self.assertTrue(m["pickupReached"])
        self.assertGreater(m["distanceTraveled"], 0.9)
        self.assertLessEqual(m["stepsUsed"], m["stepBudget"])

    def test_stop_holds_pose(self):
        r = MuJoCoSimulator().stop({})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["reached"])
        self.assertAlmostEqual(m["distanceTraveled"], 0.0, places=3)

    def test_failure_timeout_is_genuine(self):
        r = MuJoCoSimulator().pick_and_carry({"dropDistance": 8.0})
        self.assertFalse(r.success, r.to_dict())
        self.assertFalse(r.metrics["reached"])
        self.assertGreaterEqual(r.metrics["stepsUsed"], r.metrics["stepBudget"])

    def test_relay_settles_only_on_success(self):
        ex = MuJoCoExecutor()
        r = Relay(ex)
        ok = r.handle({**REQ, "idempotencyKey": "sim-ok", "payment": PAID})
        self.assertEqual(ok["status"], "completed")
        self.assertTrue(ok["settled"])

        ex2 = MuJoCoExecutor()
        r2 = Relay(ex2)
        bad = r2.handle({**REQ, "idempotencyKey": "sim-bad", "payment": PAID,
                         "params": {"dropDistance": 8.0}})
        self.assertEqual(bad["status"], "failed")
        self.assertFalse(bad["settled"])     # NO settlement on failure


if __name__ == "__main__":
    unittest.main()
