"""D3 MuJoCo executor tests (headless, deterministic, CI-friendly).

Proves the skill is REAL physics (torso travels a genuine distance, the curb is
traversed by geometry, the budget can genuinely exhaust) and that the two
required outcomes exist:

  success  -- the goal is reached within the step budget
  timeout  -- the step budget runs out before the goal (a real physics outcome,
              never a scripted success)

Also proves the payment layer settles only on success (NO settlement on
timeout).
"""
import unittest

from simulator import MuJoCoSimulator
from flow.executor import MuJoCoExecutor
from flow.relay import Relay

HAS_SIM = True  # MuJoCo is a hard dependency of this backend

REQ = {"skill": "move_forward", "robotId": "unitree-g1", "amount": "0.01"}
PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000000001"}


class TestMuJoCoWalk(unittest.TestCase):

    def test_move_forward_succeeds_and_travels(self):
        r = MuJoCoSimulator().move_forward({})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["reached"])
        self.assertGreater(m["distanceTraveled"], 0.9)
        self.assertLessEqual(m["stepsUsed"], m["stepBudget"])
        self.assertFalse(m["obstacleContact"])

    def test_navigate_obstacle_traverses_curb(self):
        r = MuJoCoSimulator().navigate_obstacle({})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["reached"])
        self.assertGreater(m["distanceTraveled"], 1.8)
        self.assertTrue(m["obstacleContact"])     # curb was actually encountered

    def test_stop_holds_pose(self):
        r = MuJoCoSimulator().stop({})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["reached"])
        self.assertAlmostEqual(m["distanceTraveled"], 0.0, places=3)

    def test_failure_timeout_is_genuine(self):
        r = MuJoCoSimulator().move_forward({"goalDistance": 5.0})
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
                         "params": {"goalDistance": 5.0}})
        self.assertEqual(bad["status"], "failed")
        self.assertFalse(bad["settled"])     # NO settlement on failure


if __name__ == "__main__":
    unittest.main()
