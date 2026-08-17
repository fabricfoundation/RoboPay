"""D4 sim-to-sim: the same skill on two independent physics engines.

Two layers of checking:

  * Static (always runs, no PyBullet needed) -- proves both backends are
    generated from the one robot spec: identical joint chain, identical link
    offsets, identical executor contract. This is what catches a drifting
    URDF on a machine where PyBullet cannot be built.

  * Dynamic (runs wherever PyBullet is importable, i.e. Linux CI) -- runs
    open_door on MuJoCo and on Bullet and requires the two engines to agree
    on the verdict, the failure reason, the handle state and the door angle.

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

CASES = ("open", "stuck", "out_of_range")


class TestSpecIsSingleSource(unittest.TestCase):
    """No physics required -- both backends must describe the same machine."""

    def setUp(self):
        self.urdf = ET.fromstring(pbsim._robot_urdf())

    def test_urdf_is_wellformed_and_named(self):
        self.assertEqual(self.urdf.get("name"), "door-arm-001")

    def test_joint_chain_matches_mjcf(self):
        names = [j.get("name") for j in self.urdf.findall("joint")]
        self.assertEqual(names,
                         list(arm_spec.ARM_JOINTS) + ["grip_l", "grip_r"])

    def test_backends_share_one_contract(self):
        from simulator_pybullet import PyBulletSimulator
        for cls in (MuJoCoSimulator, PyBulletSimulator):
            self.assertEqual(cls.ROBOT_ID, "door-arm-001")
            self.assertEqual(cls.SKILL_ID, "open_door")
            self.assertTrue(callable(cls.open_door))
        self.assertEqual(set(BACKENDS), {"mujoco", "pybullet"})

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

    def _run(self, door):
        from simulator_pybullet import PyBulletSimulator
        return PyBulletSimulator().open_door({"door": door})

    def test_success_path_completes(self):
        r = self._run("open")
        self.assertTrue(r.success, r.to_dict())
        self.assertEqual(r.metrics["engine"], "pybullet")
        self.assertEqual(r.metrics["handleState"], "gripped")
        self.assertGreater(r.metrics["doorAngle"], arm_spec.OPEN_ANGLE_MIN)

    def test_stuck_path_completes(self):
        r = self._run("stuck")
        self.assertFalse(r.success)
        self.assertIn(r.reason, ("stuck", "insufficient_open"))

    def test_out_of_range_path_completes(self):
        r = self._run("out_of_range")
        self.assertFalse(r.success)
        self.assertIn(r.reason, ("unreachable", "configuration_error"))

    def test_metric_schema_matches_mujoco(self):
        mj = MuJoCoSimulator().open_door({"door": "open"})
        bt = self._run("open")
        self.assertEqual(set(mj.metrics), set(bt.metrics))

    def test_constraint_and_urdf_calls_were_made(self):
        self._run("open")
        names = {call[0] for call in self.stub.S.calls}
        required = ("loadURDF", "createConstraint",
                     "setJointMotorControl2")
        for call in required:
            self.assertIn(call, names, call)

    def test_failure_still_blocks_settlement(self):
        from flow.relay import Relay
        out = Relay(SimExecutor("pybullet")).handle({
            "skill": "open_door", "robotId": "door-arm-001",
            "amount": "0.01", "idempotencyKey": "stub-fail",
            "payment": {"txHash": "0x" + "a" * 64, "verified": True,
                        "amount": "0.10", "network": "base-sepolia",
                        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                        "payer": "0xpayer0000000000000000000000000000000001"},
            "params": {"door": "stuck"}})
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["settled"])


@unittest.skipUnless(pbsim.available(),
                     "pybullet not importable (source-only wheel; runs in CI)")
class TestSimToSimAgreement(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from simulator_pybullet import PyBulletSimulator
        cls.mj = {c: MuJoCoSimulator().open_door({"door": c}) for c in CASES}
        cls.bt = {c: PyBulletSimulator().open_door({"door": c}) for c in CASES}

    def test_verdicts_agree(self):
        for c in CASES:
            self.assertEqual(self.mj[c].success, self.bt[c].success,
                             f"{c}: mujoco={self.mj[c].to_dict()} "
                             f"bullet={self.bt[c].to_dict()}")

    def test_failure_reasons_agree(self):
        for c in CASES:
            self.assertEqual(self.mj[c].reason, self.bt[c].reason, c)

    def test_handle_state_agrees(self):
        for c in CASES:
            self.assertEqual(self.mj[c].metrics["handleState"],
                             self.bt[c].metrics["handleState"], c)

    def test_door_angle_agrees(self):
        a = self.mj["open"].metrics["doorAngle"]
        b = self.bt["open"].metrics["doorAngle"]
        self.assertGreater(a, arm_spec.OPEN_ANGLE_MIN)
        self.assertGreater(b, arm_spec.OPEN_ANGLE_MIN)
        self.assertLess(abs(a - b), 0.05, f"angle mismatch: mujoco={a} bullet={b}")

    def test_both_engines_measure_contact_force(self):
        for eng in (self.mj, self.bt):
            self.assertGreater(eng["open"].metrics["contactForce"], 0.0)
            self.assertEqual(eng["open"].metrics["contactForce"] > 0,
                             eng["open"].success)

    def test_metric_schema_is_identical(self):
        for c in CASES:
            self.assertEqual(set(self.mj[c].metrics), set(self.bt[c].metrics), c)

    def test_engine_tag_is_reported(self):
        self.assertEqual(self.mj["open"].metrics["engine"], "mujoco")
        self.assertEqual(self.bt["open"].metrics["engine"], "pybullet")

    def test_failures_never_settle_on_either_engine(self):
        from flow.relay import Relay
        paid = {"txHash": "0x" + "a" * 64, "verified": True,
                "amount": "0.10", "network": "base-sepolia",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "payer": "0xpayer0000000000000000000000000000000001"}
        for engine in BACKENDS:
            for case, expect in (("open", True), ("stuck", False),
                                 ("out_of_range", False)):
                r = Relay(SimExecutor(engine))
                out = r.handle({"skill": "open_door",
                                "robotId": "door-arm-001", "amount": "0.01",
                                "idempotencyKey": f"{engine}-{case}",
                                "payment": paid, "params": {"door": case}})
                self.assertEqual(out["settled"], expect, f"{engine}/{case}")


if __name__ == "__main__":
    unittest.main()
