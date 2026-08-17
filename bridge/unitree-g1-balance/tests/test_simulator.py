"""D3 MuJoCo executor tests (headless, deterministic, CI-friendly).

Proves the skill is REAL physics (the torso is a genuine inverted pendulum that
the torque-limited balance PD must hold upright through a disturbance) and that
the two required outcomes exist:

  success  -- a gentle push is caught and the robot recovers upright (-> payment)
  fall     -- a hard push exceeds the actuator torque authority, the torso tips
              past FALL_PITCH and the robot falls (a real physics outcome, never
              a scripted flag -> NO settlement)

Also proves the payment layer settles only on recovered success (NO settlement
on a genuine fall).
"""
import unittest

import g1_spec
from simulator import MuJoCoSimulator
from flow.executor import MuJoCoExecutor
from flow.relay import Relay

REQ = {"skill": "balance_recover", "robotId": "unitree-g1", "amount": "0.01"}
PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000000001"}


class TestMuJoCoBalance(unittest.TestCase):

    def test_balance_recover_catches_gentle_push(self):
        r = MuJoCoSimulator().balance_recover({})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["recovered"])
        self.assertFalse(m["fell"])
        self.assertLess(m["maxPitchRad"], g1_spec.FALL_PITCH)
        self.assertLess(abs(m["pitchRad"]), g1_spec.RECOVER_PITCH)

    def test_hard_push_is_a_genuine_fall(self):
        r = MuJoCoSimulator().balance_recover({"push": g1_spec.PUSH_W_FALL})
        self.assertFalse(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["fell"])
        self.assertGreater(m["maxPitchRad"], g1_spec.FALL_PITCH)
        self.assertFalse(m["reached"])

    def test_stop_holds_pose(self):
        r = MuJoCoSimulator().stop({})
        self.assertTrue(r.success, r.to_dict())
        m = r.metrics
        self.assertTrue(m["reached"])
        self.assertLess(abs(m["pitchRad"]), 0.02)
        self.assertAlmostEqual(m["distanceTraveled"], 0.0, places=3)

    def test_relay_settles_only_on_recover(self):
        ex = MuJoCoExecutor()
        r = Relay(ex)
        ok = r.handle({**REQ, "idempotencyKey": "sim-ok", "payment": PAID})
        self.assertEqual(ok["status"], "completed")
        self.assertTrue(ok["settled"])

        ex2 = MuJoCoExecutor()
        r2 = Relay(ex2)
        bad = r2.handle({**REQ, "idempotencyKey": "sim-bad", "payment": PAID,
                         "params": {"push": g1_spec.PUSH_W_FALL}})
        self.assertEqual(bad["status"], "failed")
        self.assertFalse(bad["settled"])     # NO settlement on a genuine fall


if __name__ == "__main__":
    unittest.main()
