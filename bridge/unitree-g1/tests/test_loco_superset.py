"""B1 superset check: the shared bridge runs loco AND pick-and-carry.

The merged ``bridge/unitree-g1`` must satisfy BOTH G1 Tier-1 bounties from one
code base -- the locomotion skills (move_forward / navigate_obstacle) for the
#90 loco bounty and the pick-and-carry skill for the B1 carry bounty. This file
proves the loco skills exist, run on genuine physics, succeed when the gait
reaches the goal, fail with a real timeout otherwise, and obey the same
on-success-only settlement gate as pick-and-carry.

It re-uses the MuJoCo backend (always importable here); the engine-to-engine
agreement for these same cases is asserted by tests/test_sim2sim.py.
"""
import unittest

import g1_spec as spec
from flow.executor import SimExecutor
from flow.relay import Relay
from simulator import MuJoCoSimulator

SKILLS = {"move_forward", "navigate_obstacle", "pick_and_carry", "stop"}

PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000001"}


class TestExecutorExposesLoco(unittest.TestCase):

    def test_all_four_skills_supported(self):
        ex = SimExecutor("mujoco")
        self.assertEqual(ex.supported, SKILLS)

    def test_unknown_skill_still_rejected(self):
        ex = SimExecutor("mujoco")
        res = ex.execute("fly", {})
        self.assertFalse(res.success)
        self.assertIn("unsupported_skill", res.message)


class TestLocoRunsOnRealPhysics(unittest.TestCase):

    def setUp(self):
        self.sim = MuJoCoSimulator()

    def test_move_forward_succeeds_and_is_flat(self):
        r = self.sim.move_forward({})
        self.assertTrue(r.success, r.to_dict())
        self.assertTrue(r.metrics["reached"])
        self.assertFalse(r.metrics["obstacleContact"])   # no curb in this scene
        self.assertGreater(r.metrics["distanceTraveled"], 0.8)

    def test_move_forward_goal_distance_matches_scene(self):
        r = self.sim.move_forward({"goalDistance": 1.0})
        self.assertTrue(r.success, r.to_dict())
        # torso reaches the goal within tolerance
        self.assertGreaterEqual(r.metrics["positionEnd"][0], 1.0 - 1e-2)

    def test_navigate_obstacle_succeeds_and_touches_curb(self):
        r = self.sim.navigate_obstacle({})
        self.assertTrue(r.success, r.to_dict())
        self.assertTrue(r.metrics["reached"])
        self.assertTrue(r.metrics["obstacleContact"])     # torso crossed x=1.0
        self.assertGreater(r.metrics["distanceTraveled"], 1.8)

    def test_metric_schema_for_loco_is_consistent(self):
        for skill in ("move_forward", "navigate_obstacle"):
            m = getattr(self.sim, skill)({}).metrics
            self.assertEqual(set(m),
                             {"robotId", "skillId", "engine", "scene", "stage",
                              "positionStart", "positionEnd", "positionDelta",
                              "distanceTraveled", "stepsUsed", "stepBudget",
                              "simTime", "wallTime", "note", "goalDistance",
                              "reached", "obstacleContact"})


class TestLocoTimeoutsAreGenuine(unittest.TestCase):

    def setUp(self):
        self.sim = MuJoCoSimulator()

    def test_move_forward_times_out_when_goal_unreachable(self):
        r = self.sim.move_forward({"goalDistance": 8.0})
        self.assertFalse(r.success, r.to_dict())
        self.assertFalse(r.metrics["reached"])
        self.assertLess(r.metrics["distanceTraveled"], 3.0)  # gait capped by budget

    def test_navigate_obstacle_times_out_when_goal_unreachable(self):
        r = self.sim.navigate_obstacle({"goal_x": 8.0})
        self.assertFalse(r.success, r.to_dict())
        self.assertFalse(r.metrics["reached"])

    def test_stop_always_succeeds(self):
        r = self.sim.stop({})
        self.assertTrue(r.success, r.to_dict())
        self.assertTrue(r.metrics["reached"])


class TestLocoSettlementGate(unittest.TestCase):
    """The same on-success-only policy that guards pick_and_carry guards loco."""

    def _run(self, skill, params):
        return Relay(SimExecutor("mujoco")).handle(
            {"skill": skill, "robotId": "unitree-g1",
             "idempotencyKey": f"loco-{skill}-{params}",
             "payment": PAID, "params": dict(params)})

    def test_move_forward_success_settles(self):
        out = self._run("move_forward", {})
        self.assertEqual(out["status"], "completed")
        self.assertTrue(out["settled"])

    def test_navigate_obstacle_success_settles(self):
        out = self._run("navigate_obstacle", {})
        self.assertEqual(out["status"], "completed")
        self.assertTrue(out["settled"])

    def test_move_forward_timeout_does_not_settle(self):
        out = self._run("move_forward", {"goalDistance": 8.0})
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["settled"])

    def test_navigate_obstacle_timeout_does_not_settle(self):
        out = self._run("navigate_obstacle", {"goal_x": 8.0})
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["settled"])


if __name__ == "__main__":
    unittest.main()
