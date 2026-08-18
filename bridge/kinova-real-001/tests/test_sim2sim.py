"""D4 sim-to-sim: the same skill on two independent physics engines.

Two layers of checking:

  * Static (always runs, no PyBullet needed) -- proves both backends are
    generated from the one robot spec: identical joint chain, identical link
    offsets, identical executor contract. This is what catches a drifting
    URDF on a machine where PyBullet cannot be built.

  * Dynamic (runs wherever PyBullet is importable, i.e. Linux CI) -- runs
    pick_object on MuJoCo and on Bullet and requires the two engines to agree
    on the verdict, the failure reason, the grasp state and the lift
    distance.

PyBullet publishes a source distribution only, so it compiles on Linux CI but
generally not on a stock Windows box. The dynamic layer skips there rather
than pretending to pass.
"""
import sys
import unittest
import xml.etree.ElementTree as ET

import arm_spec
import simulator_pybullet as pbsim
from flow.executor import BACKENDS, SimExecutor
from simulator import MuJoCoSimulator

CASES = ("cube", "unreachable", "collision", "timeout")


class TestSpecIsSingleSource(unittest.TestCase):
    """No physics required -- both backends must describe the same machine."""

    def setUp(self):
        self.urdf = ET.fromstring(pbsim._robot_urdf())

    def test_urdf_is_wellformed_and_named(self):
        self.assertEqual(self.urdf.get("name"), "kinova-real-001")

    def test_joint_chain_matches_mjcf(self):
        names = [j.get("name") for j in self.urdf.findall("joint")]
        self.assertEqual(names,
                         list(arm_spec.ARM_JOINTS) + ["grip_l", "grip_r"])

    def test_link_offsets_come_from_the_spec(self):
        origins = {j.get("name"): j.find("origin").get("xyz")
                   for j in self.urdf.findall("joint")}
        self.assertEqual(origins["elbow"].split()[0], str(arm_spec.LINK1))
        self.assertEqual(origins["wristp"].split()[0], str(arm_spec.LINK2))
        self.assertEqual(origins["grip_l"].split()[2], f"-{arm_spec.GRIP_MID}")
        self.assertEqual(origins["grip_r"].split()[2], f"-{arm_spec.GRIP_MID}")

    def test_gripper_axes_are_opposed(self):
        axes = {j.get("name"): j.find("axis").get("xyz")
                for j in self.urdf.findall("joint")}
        self.assertEqual(axes["grip_l"], "0 1 0")
        self.assertEqual(axes["grip_r"], "0 -1 0")

    def test_backends_share_one_contract(self):
        from simulator_pybullet import PyBulletSimulator
        for cls in (MuJoCoSimulator, PyBulletSimulator):
            self.assertEqual(cls.ROBOT_ID, "kinova-real-001")
            self.assertEqual(cls.SKILL_ID, "pick_object")
            self.assertTrue(callable(cls.pick_object))
        self.assertEqual(set(BACKENDS), {"mujoco", "pybullet"})

    def test_keyframes_are_solved_not_guessed(self):
        """Grasp frame must place the pads around the cube's centre of mass."""
        _x, _y, z = arm_spec.forward(arm_spec.KEYFRAMES["grasp"])
        self.assertAlmostEqual(z - arm_spec.GRIP_MID, arm_spec.CUBE_HALF, places=3)

    def test_unknown_engine_is_rejected(self):
        with self.assertRaises(ValueError):
            SimExecutor("gazebo")


@unittest.skipIf(pbsim.available(), "real pybullet present; stub not needed")
class TestPyBulletBackendContract(unittest.TestCase):
    """Walk every PyBullet call the backend makes, without PyBullet.

    Catches misspelled functions, wrong keyword names and wrong return-tuple
    indices on developer machines where the wheel cannot be built. Physics
    agreement is asserted separately by TestSimToSimAgreement on CI.
    """

    def setUp(self):
        import tests.bullet_stub as stub
        self._saved = sys.modules.get("pybullet")
        sys.modules["pybullet"] = stub
        self.stub = stub

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("pybullet", None)
        else:                                          # pragma: no cover
            sys.modules["pybullet"] = self._saved

    def _run(self, obj):
        from simulator_pybullet import PyBulletSimulator
        return PyBulletSimulator().pick_object({"object": obj})

    def test_success_path_completes(self):
        r = self._run("cube")
        self.assertTrue(r.success, r.to_dict())
        self.assertEqual(r.metrics["engine"], "pybullet")
        self.assertEqual(r.metrics["graspState"], "attached")
        self.assertGreater(r.metrics["objectLifted"], arm_spec.LIFT_MIN)
        self.assertGreater(r.metrics["contactForce"], 0.0)

    def test_unreachable_path_completes(self):
        r = self._run("unreachable")
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "unreachable")

    def test_collision_path_completes(self):
        r = self._run("collision")
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "collision")

    def test_timeout_path_completes(self):
        r = self._run("timeout")
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "timeout")

    def test_metric_schema_matches_mujoco(self):
        mj = MuJoCoSimulator().pick_object({"object": "cube"})
        bt = self._run("cube")
        self.assertEqual(set(mj.metrics), set(bt.metrics))

    def test_constraint_and_urdf_calls_were_made(self):
        self._run("cube")
        for call in ("loadURDF", "createConstraint", "changeConstraint",
                     "setCollisionFilterGroupMask", "setJointMotorControl2"):
            self.assertIn(call, self.stub.S.calls, call)

    def test_failure_still_blocks_settlement(self):
        from flow.relay import Relay
        out = Relay(SimExecutor("pybullet")).handle({
            "skill": "pick_object", "robotId": "kinova-real-001",
            "amount": "0.01", "idempotencyKey": "stub-fail",
            "payment": {"txHash": "0x" + "a" * 64, "verified": True,
                        "amount": "0.10", "network": "base-sepolia",
                        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                        "payer": "0xpayer0000000000000000000000000000000001"},
            "params": {"object": "collision"}})
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["settled"])


@unittest.skipUnless(pbsim.available(),
                     "pybullet not importable (source-only wheel; runs in CI)")
class TestSimToSimAgreement(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from simulator_pybullet import PyBulletSimulator
        cls.mj = {c: MuJoCoSimulator().pick_object({"object": c}) for c in CASES}
        cls.bt = {c: PyBulletSimulator().pick_object({"object": c}) for c in CASES}

    def test_verdicts_agree(self):
        for c in CASES:
            self.assertEqual(self.mj[c].success, self.bt[c].success,
                             f"{c}: mujoco={self.mj[c].to_dict()} "
                             f"bullet={self.bt[c].to_dict()}")

    def test_failure_reasons_agree(self):
        for c in CASES:
            self.assertEqual(self.mj[c].reason, self.bt[c].reason, c)

    def test_grasp_state_agrees(self):
        for c in CASES:
            self.assertEqual(self.mj[c].metrics["graspState"],
                             self.bt[c].metrics["graspState"], c)

    def test_lift_distance_agrees(self):
        a = self.mj["cube"].metrics["objectLifted"]
        b = self.bt["cube"].metrics["objectLifted"]
        self.assertGreater(a, arm_spec.LIFT_MIN)
        self.assertGreater(b, arm_spec.LIFT_MIN)
        self.assertLess(abs(a - b), 0.03, f"lift mismatch: mujoco={a} bullet={b}")

    def test_both_engines_measure_contact_force(self):
        for eng in (self.mj, self.bt):
            self.assertGreater(eng["cube"].metrics["contactForce"], 0.0)
            self.assertEqual(eng["cube"].metrics["contactForce"] > 0,
                             eng["cube"].success)

    def test_metric_schema_is_identical(self):
        for c in CASES:
            self.assertEqual(set(self.mj[c].metrics), set(self.bt[c].metrics), c)

    def test_engine_tag_is_reported(self):
        self.assertEqual(self.mj["cube"].metrics["engine"], "mujoco")
        self.assertEqual(self.bt["cube"].metrics["engine"], "pybullet")

    def test_failures_never_settle_on_either_engine(self):
        from flow.relay import Relay
        paid = {"txHash": "0x" + "a" * 64, "verified": True,
                "amount": "0.10", "network": "base-sepolia",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "payer": "0xpayer0000000000000000000000000000000001"}
        for engine in BACKENDS:
            for case, expect in (("cube", True), ("unreachable", False),
                                 ("collision", False), ("timeout", False)):
                r = Relay(SimExecutor(engine))
                out = r.handle({"skill": "pick_object",
                                "robotId": "kinova-real-001", "amount": "0.01",
                                "idempotencyKey": f"{engine}-{case}",
                                "payment": paid, "params": {"object": case}})
                self.assertEqual(out["settled"], expect, f"{engine}/{case}")


if __name__ == "__main__":
    unittest.main()
