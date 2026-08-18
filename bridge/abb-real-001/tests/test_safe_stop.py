"""Acceptance #5 (bounded policy + safe-stop interruptible) -- real MuJoCo.

The real vendor arm simulator must fail-closed on every bounty-relevant
condition (unreachable / collision / timeout): it returns a PickResult with
success=False and a reason, and it MUST NOT raise or settle. It must also
terminate within its step budget (bounded), proving the action is
interruptible by a safe stop. The actual "no settlement on failure" guarantee
is enforced by the shared Go Tunnel (see test_x402_no_settlement.py); this test
proves the simulator half of the contract.
"""
from __future__ import annotations

import unittest

from simulator import MuJoCoSimulator
from arm_spec import PickResult

BUDGET_HARD_CAP = 10000  # guards against any runaway loop; real runs are << this


class SafeStopTests(unittest.TestCase):
    def setUp(self):
        self.sim = MuJoCoSimulator()

    def test_cube_succeeds(self):
        res = self.sim.pick_object({"object": "cube"})
        self.assertIsInstance(res, PickResult)
        self.assertTrue(res.success, f"cube should succeed, got {res.reason}")
        self.assertLess(res.metrics["steps"], BUDGET_HARD_CAP)

    def test_unreachable_fail_closed(self):
        res = self.sim.pick_object({"object": "unreachable"})
        self.assertIsInstance(res, PickResult)
        self.assertFalse(res.success)
        self.assertEqual(res.reason, "unreachable")
        self.assertLess(res.metrics["steps"], BUDGET_HARD_CAP)

    def test_collision_fail_closed(self):
        res = self.sim.pick_object({"object": "collision"})
        self.assertIsInstance(res, PickResult)
        self.assertFalse(res.success)
        self.assertEqual(res.reason, "collision")
        self.assertLess(res.metrics["steps"], BUDGET_HARD_CAP)

    def test_timeout_fail_closed(self):
        res = self.sim.pick_object({"object": "timeout"})
        self.assertIsInstance(res, PickResult)
        self.assertFalse(res.success)
        self.assertEqual(res.reason, "timeout")
        self.assertLess(res.metrics["steps"], BUDGET_HARD_CAP)

    def test_no_exception_on_failure(self):
        # fail-closed must never raise; graceful PickResult only
        for scene in ("unreachable", "collision", "timeout"):
            try:
                res = self.sim.pick_object({"object": scene})
            except Exception as exc:
                self.fail(f"{scene} raised {type(exc).__name__}: {exc}")
            self.assertIsInstance(res, PickResult)


if __name__ == "__main__":
    unittest.main()
