"""D4 sim-to-sim: the same skill on two independent physics engines.

Two layers of checking:

  * Static (always runs, no PyBullet needed) -- proves both backends are
    generated from the one robot spec: identical joint chain (now including the
    torso_pitch inverted-pendulum hinge), identical link offsets, identical
    executor contract. This is what catches a drifting URDF on a machine where
    PyBullet cannot be built.

  * Dynamic (runs wherever PyBullet is importable, i.e. Linux CI) -- runs every
    skill on MuJoCo and on Bullet and requires the two engines to agree on the
    verdict (recovered / fell), the reached flag, the fell flag and the reported
    engine tag.

The balance-recover task is a genuine inverted pendulum: a disturbance injects
an angular velocity about the hip line and a torque-limited PD (capped well
below the peak gravity torque) fights to keep the torso upright. A gentle push
stays where the capped PD can hold it -> the robot recovers (success ->
settlement). A hard push exceeds the actuator authority -> the torso tips past
FALL_PITCH and falls (genuine physics failure -> NO settlement). Both engines
implement the identical joint, gains and torque cap, so the recover/fall
verdict is a property of the definition, not of one solver.

PyBullet publishes a source distribution only, so it compiles on Linux CI but
generally not on a stock Windows box. The dynamic layer skips there rather
than pretending to pass.
"""
import sys
import unittest
import xml.etree.ElementTree as ET

import g1_spec
import simulator_pybullet as pbsim
from flow.executor import BACKENDS, SimExecutor
from simulator import MuJoCoSimulator

# (skill, params, expect_success) -- the genuine outcomes of the planar biped.
#   balance_recover {}    -> gentle push, torque-limited PD catches it (recover)
#   stop {}               -> stance hold, always succeeds
#   balance_recover{push:8.0} -> hard push exceeds actuator authority -> falls
CASES = [
    ("balance_recover", {}, True),
    ("stop", {}, True),
    ("balance_recover", {"push": g1_spec.PUSH_W_FALL}, False),   # real fall
]


class TestSpecIsSingleSource(unittest.TestCase):
    """No physics required -- both backends must describe the same machine."""

    def setUp(self):
        self.urdf = ET.fromstring(pbsim._robot_urdf())

    def test_urdf_is_wellformed_and_named(self):
        self.assertEqual(self.urdf.get("name"), "unitree-g1")

    def test_joint_chain_matches_mjcf(self):
        names = [j.get("name") for j in self.urdf.findall("joint")]
        # torso_x (prismatic) -> torso_pitch (the inverted-pendulum hinge) ->
        # the four leg hinges, in that fixed order.
        self.assertEqual(names,
                         ["torso_x", "torso_pitch"] + list(g1_spec.LEG_JOINTS))

    def test_link_offsets_come_from_the_spec(self):
        origins = {j.get("name"): j.find("origin").get("xyz")
                   for j in self.urdf.findall("joint")}
        self.assertEqual(origins["left_knee"].split()[2], f"-{g1_spec.THIGH_LEN:.3f}")
        self.assertEqual(origins["left_hip"].split()[2], f"-{g1_spec.TORSO_H / 2:.3f}")
        self.assertEqual(origins["torso_x"].split()[2], f"{g1_spec.HIP_Z:.3f}")
        # HIP_X_OFFSET is a bare float in the URDF template (no :.3f), so compare
        # as floats, not as formatted strings.
        self.assertAlmostEqual(float(origins["left_hip"].split()[1]),
                               g1_spec.HIP_X_OFFSET, places=6)

    def test_leg_axes_are_y(self):
        axes = {j.get("name"): j.find("axis").get("xyz")
                for j in self.urdf.findall("joint")}
        for name in ("torso_pitch",) + g1_spec.LEG_JOINTS:
            self.assertEqual(axes[name], "0 1 0")

    def test_backends_share_one_contract(self):
        from simulator_pybullet import PyBulletSimulator
        for cls in (MuJoCoSimulator, PyBulletSimulator):
            self.assertEqual(cls.ROBOT_ID, "unitree-g1")
            self.assertEqual(cls.SKILL_ID, "balance_recover")
            self.assertTrue(callable(cls.balance_recover))
            self.assertTrue(callable(cls.stop))
        self.assertEqual(set(BACKENDS), {"mujoco", "pybullet"})

    def test_model_is_not_a_replayed_animation(self):
        """Gait/balance is an open-loop IK + torque-limited PD, not animation."""
        from flow import profiles
        determinism = profiles.robot_profile()["simulation"]["determinism"]
        self.assertFalse(determinism["replayedAnimation"])
        self.assertTrue(determinism["policyDriven"])
        # reference the import so linters keep it; not otherwise used
        self.assertIsNotNone(g1_spec.STAGE_STEPS)

    def test_unknown_engine_is_rejected(self):
        with self.assertRaises(ValueError):
            SimExecutor("gazebo")


@unittest.skipIf(pbsim.available(), "real pybullet present; stub not needed")
class TestPyBulletBackendContract(unittest.TestCase):
    """Walk every PyBullet call the backend makes, without PyBullet.

    Catches misspelled functions, wrong keyword names and wrong return-tuple
    indices on developer machines where the wheel cannot be built. Physics
    agreement is asserted separately by TestSimToSimAgreement on CI. The stub is
    a contract check only: it follows the position/effort-control targets the
    backend issues but does not integrate dynamics, so the genuine fall (which
    needs real gravity) is asserted by the dynamic layer, not here.
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

    def _run(self, skill, params):
        from simulator_pybullet import PyBulletSimulator
        return PyBulletSimulator().run(skill, params)

    def test_recover_path_completes(self):
        r = self._run("balance_recover", {})
        self.assertTrue(r.success, r.to_dict())
        self.assertEqual(r.metrics["engine"], "pybullet")
        self.assertTrue(r.metrics["recovered"])
        self.assertFalse(r.metrics["fell"])
        self.assertLess(r.metrics["maxPitchRad"], g1_spec.FALL_PITCH)

    def test_stop_holds_pose(self):
        r = self._run("stop", {})
        self.assertTrue(r.success, r.to_dict())
        self.assertEqual(r.metrics["engine"], "pybullet")
        self.assertLess(abs(r.metrics["pitchRad"]), 0.02)

    def test_metric_schema_matches_mujoco(self):
        mj = MuJoCoSimulator().balance_recover({})
        bt = self._run("balance_recover", {})
        self.assertEqual(set(mj.metrics), set(bt.metrics))

    def test_constraint_and_urdf_calls_were_made(self):
        self._run("balance_recover", {})
        S = self.stub.S
        for call in ("loadURDF", "setJointMotorControl2", "stepSimulation",
                     "setCollisionFilterGroupMask"):
            self.assertIn(call, S.calls, call)

    def test_failure_still_blocks_settlement(self):
        from flow.executor import MockExecutor
        from flow.relay import Relay
        # A failed execution (here via the mock executor that always fails the
        # balance skill) must never reach settlement -- the relay-level
        # no-settle gate, exercised with the real relay+payment code.
        out = Relay(MockExecutor(fail_skill="balance_recover")).handle({
            "skill": "balance_recover", "robotId": "unitree-g1",
            "idempotencyKey": "stub-fail",
            "payment": {"txHash": "0x" + "a" * 64, "verified": True,
                        "amount": "0.10", "network": "eip155:84532",
                        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                        "payer": "0xpayer0000000000000000000000000000000001"},
            "params": {}})
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["settled"])


@unittest.skipUnless(pbsim.available(),
                     "pybullet not importable (source-only wheel; runs in CI)")
class TestSimToSimAgreement(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from simulator_pybullet import PyBulletSimulator
        cls.mj = {c[0]: MuJoCoSimulator().run(c[0], c[1]) for c in CASES}
        cls.bt = {c[0]: PyBulletSimulator().run(c[0], c[1]) for c in CASES}

    def test_verdicts_agree(self):
        for skill, _params, expect in CASES:
            self.assertEqual(self.mj[skill].success, expect, skill)
            self.assertEqual(self.bt[skill].success, expect,
                             f"bullet disagrees on {skill}")

    def test_reached_flags_agree(self):
        for skill, _params, _expect in CASES:
            self.assertEqual(self.mj[skill].metrics["reached"],
                             self.bt[skill].metrics["reached"], skill)

    def test_fell_flags_agree(self):
        for skill, _params, _expect in CASES:
            self.assertEqual(self.mj[skill].metrics["fell"],
                             self.bt[skill].metrics["fell"], skill)

    def test_recover_flags_agree(self):
        for skill, _params, _expect in CASES:
            self.assertEqual(self.mj[skill].metrics["recovered"],
                             self.bt[skill].metrics["recovered"], skill)

    def test_peak_pitch_agrees(self):
        for skill, _params, _expect in CASES:
            a = self.mj[skill].metrics["maxPitchRad"]
            b = self.bt[skill].metrics["maxPitchRad"]
            self.assertLess(abs(a - b), 0.20, f"pitch drift: {skill}")

    def test_engine_tag_is_reported(self):
        self.assertEqual(self.mj["balance_recover"].metrics["engine"], "mujoco")
        self.assertEqual(self.bt["balance_recover"].metrics["engine"], "pybullet")

    def test_metric_schema_is_identical(self):
        for skill, _params, _expect in CASES:
            self.assertEqual(set(self.mj[skill].metrics),
                             set(self.bt[skill].metrics), skill)

    def test_failures_never_settle_on_either_engine(self):
        from flow.relay import Relay
        paid = {"txHash": "0x" + "a" * 64, "verified": True,
                "amount": "0.10", "network": "eip155:84532",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "payer": "0xpayer0000000000000000000000000000000001"}
        for engine in BACKENDS:
            for skill, params, expect in CASES:
                r = Relay(SimExecutor(engine))
                out = r.handle({"skill": skill, "robotId": "unitree-g1",
                                "idempotencyKey": f"{engine}-{skill}",
                                "payment": paid, "params": dict(params)})
                self.assertEqual(out["settled"], expect, f"{engine}/{skill}")


if __name__ == "__main__":
    unittest.main()
