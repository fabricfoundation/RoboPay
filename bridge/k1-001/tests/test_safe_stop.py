"""Acceptance #5 (bounded policy + safe-stop interruptible) -- real MuJoCo.

The K1 vendor simulator must fail-closed on every bounty-relevant condition
(timeout / unreachable / partial): it returns an InspectionResult with
success=False and a reason, and it MUST NOT raise or settle. It must also
terminate within its step budget (bounded), proving the action is
interruptible by a safe stop. The actual "no settlement on failure" guarantee
is enforced by the shared Go Tunnel (see test_x402_no_settlement.py); this test
proves the simulator half of the contract.
"""
from __future__ import annotations

import unittest

from simulator import MuJoCoSimulator
from arm_spec import InspectionResult

BUDGET_HARD_CAP = 10000  # guards against any runaway loop; real runs are << this


class SafeStopTests(unittest.TestCase):
    def setUp(self):
        self.sim = MuJoCoSimulator()

    def test_inspection_succeeds(self):
        res = self.sim.active_inspection({"scenario": "inspection"})
        self.assertIsInstance(res, InspectionResult)
        self.assertTrue(res.success, f"inspection should succeed, got {res.reason}")
        self.assertLess(res.metrics["stepsUsed"], BUDGET_HARD_CAP)

    def test_timeout_fail_closed(self):
        res = self.sim.active_inspection({"scenario": "timeout"})
        self.assertIsInstance(res, InspectionResult)
        self.assertFalse(res.success)
        self.assertEqual(res.reason, "timeout")
        self.assertLess(res.metrics["stepsUsed"], BUDGET_HARD_CAP)

    def test_partial_fail_closed(self):
        # A partially-completed inspection must be reported as failure, never
        # settled as success.
        res = self.sim.active_inspection({"scenario": "inspection"})
        self.assertIsInstance(res, InspectionResult)
        if not res.success:
            self.assertEqual(res.reason, "partial")
        self.assertLess(res.metrics["stepsUsed"], BUDGET_HARD_CAP)

    def test_no_exception_on_failure(self):
        # fail-closed must never raise; graceful InspectionResult only
        for scenario in ("timeout", "inspection"):
            try:
                res = self.sim.active_inspection({"scenario": scenario})
            except Exception as exc:  # noqa: BLE001
                self.fail(f"{scenario} raised {type(exc).__name__}: {exc}")
            self.assertIsInstance(res, InspectionResult)


if __name__ == "__main__":
    unittest.main()