"""D4 sim-to-sim: the same skill on two independent physics engines.

Two layers of checking:

  * Static (always runs, no PyBullet needed) -- proves both backends are
    generated from the one robot spec: identical joint chain, identical link
    offsets, identical executor contract. This is what catches a drifting
    URDF on a machine where PyBullet cannot be built.

  * Dynamic (runs wherever PyBullet is importable, i.e. Linux CI) -- runs
    active_inspection on MuJoCo and on Bullet and requires the two engines to
    agree on the verdict, the failure reason and the metric schema.

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

SCENARIOS = ("inspection", "timeout", "single_target")


class TestSpecIsSingleSource(unittest.TestCase):
    """No physics required -- both backends must describe the same machine."""

    def setUp(self):
        self.urdf = ET.fromstring(pbsim._robot_urdf())

    def test_urdf_is_wellformed_and_named(self):
        self.assertEqual(self.urdf.get("name"), "k1-001")

    def test_joint_chain_matches_mjcf(self):
        names = [j.get("name") for j in self.urdf.findall("joint")]
        self.assertEqual(names, list(arm_spec.ARM_JOINTS))

    def test_link_offsets_come_from_the_spec(self):
        origins = {j.get("name"): j.find("origin").get("xyz")
                   for j in self.urdf.findall("joint")}
        self.assertEqual(origins["elbow"].split()[0], str(arm_spec.LINK1))
        self.assertEqual(origins["wrist_pitch"].split()[0], str(arm_spec.LINK2))
        self.assertEqual(origins["wrist_roll"].split()[2], f"-{arm_spec.LINK3}")

    def test_camera_mount_has_pan_joint(self):
        names = [j.get("name") for j in self.urdf.findall("joint")]
        self.assertIn("cam_pan", names)
        parents = {j.get("name"): j.find("parent").get("link")
                   for j in self.urdf.findall("joint")}
        children = {j.get("name"): j.find("child").get("link")
                    for j in self.urdf.findall("joint")}
        # A revolute joint may never connect a body to itself.
        for jname in names:
            self.assertNotEqual(parents[jname], children[jname], jname)

    def test_backends_share_one_contract(self):
        from simulator_pybullet import PyBulletSimulator
        for cls in (MuJoCoSimulator, PyBulletSimulator):
            self.assertEqual(cls.ROBOT_ID, "k1-001")
            self.assertEqual(cls.SKILL_ID, "active_inspection")
            self.assertTrue(callable(cls.active_inspection))
        self.assertEqual(set(BACKENDS), {"mujoco", "pybullet"})

    def test_keyframes_cover_every_target_and_joint(self):
        """The static keyframe table must include home + one entry per target,
        and every pose must only use spec joints. (Runtime may add derived
        `above_*` entries -- the sims extend KEYFRAMES dynamically.)"""
        required = {"home", "target_left", "target_center", "target_right"}
        self.assertTrue(required.issubset(set(arm_spec.KEYFRAMES)),
                        f"missing {required - set(arm_spec.KEYFRAMES)}")
        for name, pose in arm_spec.KEYFRAMES.items():
            self.assertEqual(set(pose), set(arm_spec.ARM_JOINTS), name)

    def test_unknown_engine_is_rejected(self):
        with self.assertRaises(ValueError):
            SimExecutor("gazebo")


try:
    import mujoco  # noqa: F401
    import numpy as np  # noqa: F401
    HAVE_MUJOCO = True
except Exception:                                    # pragma: no cover
    HAVE_MUJOCO = False


@unittest.skipUnless(HAVE_MUJOCO, "mujoco not installed")
class TestKeyframesReachTargets(unittest.TestCase):
    """Real-MuJoCo check that every target keyframe actually puts the camera
    within confirm distance of its named target (the acceptance #2 check).
    arm_spec.forward() is a closed-form approximation; the authoritative FK is
    the MuJoCo site transform, so we assert against the engine itself."""

    def test_each_keyframe_confirms_its_target(self):
        sim = MuJoCoSimulator()
        sim._build({"targets": [("left", 0.30, 0.10),
                                ("center", 0.30, 0.18),
                                ("right", 0.30, 0.26)]})
        for name in ("left", "center", "right"):
            pose = arm_spec.KEYFRAMES[f"target_{name}"]
            sim._apply(pose)
            mujoco.mj_forward(sim.model, sim.data)
            cam = sim.data.site_xpos[sim._cam_site]
            tgt = sim.data.site_xpos[sim._target_sites[name]]
            dist = float(np.linalg.norm(tgt - cam))
            self.assertGreaterEqual(dist, arm_spec.DISTANCE_MIN,
                                    f"{name}: too close ({dist:.3f}m)")
            self.assertLessEqual(dist, arm_spec.CONFIRM_DISTANCE_MAX,
                                 f"{name}: too far ({dist:.3f}m)")


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

    def _run(self, scenario):
        from simulator_pybullet import PyBulletSimulator
        return PyBulletSimulator().active_inspection({"scenario": scenario})

    def test_success_path_completes(self):
        r = self._run("inspection")
        self.assertTrue(r.success, r.to_dict())
        self.assertEqual(r.reason, "all_targets_confirmed")
        self.assertEqual(r.metrics["engine"], "pybullet")
        self.assertEqual(r.metrics["cameraState"], "confirmed")

    def test_single_target_path_completes(self):
        r = self._run("single_target")
        self.assertTrue(r.success, r.to_dict())
        self.assertEqual(r.metrics["target"], "center")

    def test_timeout_path_completes(self):
        r = self._run("timeout")
        self.assertFalse(r.success)
        self.assertEqual(r.reason, "timeout")

    def test_metric_schema_matches_mujoco(self):
        mj = MuJoCoSimulator().active_inspection({"scenario": "inspection"})
        bt = self._run("inspection")
        self.assertEqual(set(mj.metrics), set(bt.metrics))

    def test_urdf_and_robot_calls_were_made(self):
        self._run("inspection")
        for call in ("loadURDF", "getNumJoints", "getJointInfo",
                     "setJointMotorControl2", "resetJointState",
                     "stepSimulation", "getLinkState"):
            self.assertIn(call, self.stub.S.calls, call)

    def test_target_bodies_were_created(self):
        self._run("inspection")
        self.assertIn("createMultiBody", self.stub.S.calls)
        self.assertEqual(len(self.stub.S.targets), 3)

    def test_failure_still_blocks_settlement(self):
        from flow.relay import Relay
        out = Relay(SimExecutor("pybullet")).handle({
            "skill": "active_inspection", "robotId": "k1-001",
            "amount": "0.01", "idempotencyKey": "stub-fail",
            "payment": {"txHash": "0x" + "a" * 64, "verified": True,
                        "amount": "0.10", "network": "base-sepolia",
                        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                        "payer": "0xpayer0000000000000000000000000000000001"},
            "params": {"scenario": "timeout"}})
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["settled"])


@unittest.skipUnless(pbsim.available(),
                     "pybullet not importable (source-only wheel; runs in CI)")
class TestSimToSimAgreement(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from simulator_pybullet import PyBulletSimulator
        cls.mj = {c: MuJoCoSimulator().active_inspection({"scenario": c})
                  for c in SCENARIOS}
        cls.bt = {c: PyBulletSimulator().active_inspection({"scenario": c})
                  for c in SCENARIOS}

    def test_verdicts_agree(self):
        for c in SCENARIOS:
            self.assertEqual(self.mj[c].success, self.bt[c].success,
                             f"{c}: mujoco={self.mj[c].to_dict()} "
                             f"bullet={self.bt[c].to_dict()}")

    def test_failure_reasons_agree(self):
        for c in SCENARIOS:
            self.assertEqual(self.mj[c].reason, self.bt[c].reason, c)

    def test_metric_schema_is_identical(self):
        for c in SCENARIOS:
            self.assertEqual(set(self.mj[c].metrics), set(self.bt[c].metrics), c)

    def test_engine_tag_is_reported(self):
        self.assertEqual(self.mj["inspection"].metrics["engine"], "mujoco")
        self.assertEqual(self.bt["inspection"].metrics["engine"], "pybullet")

    def test_failures_never_settle_on_either_engine(self):
        from flow.relay import Relay
        paid = {"txHash": "0x" + "a" * 64, "verified": True,
                "amount": "0.10", "network": "base-sepolia",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "payer": "0xpayer0000000000000000000000000000000001"}
        for engine in BACKENDS:
            for case, expect in (("inspection", True), ("timeout", False),
                                 ("single_target", True)):
                r = Relay(SimExecutor(engine))
                out = r.handle({"skill": "active_inspection",
                                "robotId": "k1-001", "amount": "0.01",
                                "idempotencyKey": f"{engine}-{case}",
                                "payment": paid,
                                "params": {"scenario": case}})
                self.assertEqual(out["settled"], expect, f"{engine}/{case}")


if __name__ == "__main__":
    unittest.main()