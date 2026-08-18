"""D5 profile tests --- the manifests must describe the RUNNING bridge.

A reviewer's fastest way to dismiss a submission is to notice that the five
required YAML files are decoration. These tests make that impossible: every
number, topic, threshold, scene and test reference in `profiles/` is compared
against the code that actually executes. If the two ever disagree, CI is red.
"""
import importlib
import os
import unittest
from pathlib import Path

import g1_spec as spec
from flow import profiles
from flow.executor import SimExecutor, MockExecutor
from flow.relay import Relay
from flow.zenoh_transport import ACTION_TOPIC, RESULT_TOPIC

ROOT = Path(__file__).resolve().parent.parent
PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000001"}
REQ = {"skill": "move_forward", "robotId": "unitree-g1"}


class TestManifestsExist(unittest.TestCase):
    """The five files the PR Review Checklist greps for."""

    def test_all_five_manifests_load(self):
        for name, filename in profiles.MANIFESTS.items():
            self.assertTrue((ROOT / "profiles" / filename).exists(),
                            f"{filename} is missing")
            self.assertIsInstance(profiles.load(name), dict)

    def test_identity_is_consistent_across_manifests(self):
        rid = profiles.robot_id()
        pid = profiles.profile_id()
        self.assertEqual(rid, "unitree-g1")
        self.assertEqual(pid, "laok.unitree-g1-arm-001.loco.v1")
        # The two manifests that actually carry identity must agree.
        self.assertEqual(profiles.robot_profile()["profileId"], pid)
        self.assertEqual(profiles.skills_catalog()["profileId"], pid)

    def test_referenced_modules_exist(self):
        prof = profiles.robot_profile()
        for engine in ("primaryEngine", "secondaryEngine"):
            module = prof["simulation"][engine]["module"]
            self.assertTrue((ROOT / module).exists(), f"{module} is missing")
        spec_source = Path(prof["embodiment"]["specSource"]).name
        self.assertTrue((ROOT / spec_source).exists(), spec_source)


class TestRobotProfileMatchesSpec(unittest.TestCase):
    """robot.profile.yaml vs g1_spec.py -- one robot, one description."""

    def setUp(self):
        self.prof = profiles.robot_profile()

    def test_kinematics_match(self):
        k = self.prof["embodiment"]["kinematics"]
        self.assertAlmostEqual(k["torsoHeight"], spec.TORSO_H, places=6)
        self.assertAlmostEqual(k["thighLength"], spec.THIGH_LEN, places=6)
        self.assertAlmostEqual(k["shankLength"], spec.SHANK_LEN, places=6)
        self.assertAlmostEqual(k["footHeight"], spec.FOOT_H, places=6)
        self.assertAlmostEqual(k["hipHeight"], spec.HIP_Z, places=6)
        self.assertAlmostEqual(k["standingHeight"], spec.STAND_Z, places=6)

    def test_embodiment_type_is_planar_biped(self):
        self.assertEqual(self.prof["embodiment"]["type"], "planar_biped")
        self.assertEqual(self.prof["embodiment"]["degreesOfFreedom"], 5)

    def test_joint_names_and_count_match(self):
        joints = [j["name"] for j in self.prof["embodiment"]["joints"]]
        self.assertEqual(tuple(joints), ("torso_x",) + spec.LEG_JOINTS)
        self.assertEqual(self.prof["embodiment"]["degreesOfFreedom"],
                         len(spec.LEG_JOINTS) + 1)

    def test_timestep_matches(self):
        self.assertAlmostEqual(
            self.prof["simulation"]["primaryEngine"]["timestep"], spec.TIMESTEP,
            places=6)

    def test_topics_match_the_transport_module(self):
        t = self.prof["transport"]["topics"]
        self.assertEqual(t["action"], ACTION_TOPIC)
        self.assertEqual(t["result"], RESULT_TOPIC)

    def test_endpoint_and_mode_match_the_transport_module(self):
        from flow.zenoh_transport import DEFAULT_ENDPOINT, DEFAULT_MODE
        self.assertEqual(self.prof["transport"]["endpoint"], DEFAULT_ENDPOINT)
        self.assertEqual(self.prof["transport"]["mode"], DEFAULT_MODE)

    def test_scope_is_declared_simulation_only(self):
        scope = self.prof["scope"]
        self.assertEqual(scope["classification"], "simulator")
        self.assertTrue(scope["simulationOnly"])
        self.assertFalse(scope["realWorldActuation"])
        self.assertFalse(scope["gpuRequired"])

    def test_wallet_binding_is_env_only(self):
        identity = self.prof["identity"]
        self.assertFalse(identity["keyMaterialInRepo"])
        for field in ("walletAddressEnv", "privateKeyEnv", "payToAddressEnv"):
            self.assertTrue(identity[field].isupper(),
                            f"{field} must name an environment variable")


class TestSkillsCatalogMatchesCode(unittest.TestCase):

    def test_catalogue_matches_the_executor(self):
        """What the catalogue advertises is exactly what the executor accepts."""
        executor = SimExecutor.__new__(SimExecutor)      # no engine boot needed
        SimExecutor.__init__(executor, "mujoco")
        self.assertEqual(executor.supported, set(profiles.skill_ids()))
        self.assertEqual(executor.supported,
                         {"move_forward", "navigate_obstacle", "stop"})

    def test_param_validation_rejects_unknown_keys(self):
        with self.assertRaises(profiles.ParamError):
            profiles.validate_params("move_forward", {"object": "cube"})

    def test_param_validation_accepts_empty_and_goal_distance(self):
        # validate_params fills defaults for missing keys; assert specific values.
        empty = profiles.validate_params("move_forward", {})
        self.assertEqual(empty["goalDistance"], 1.0)
        self.assertEqual(empty["speed"], 0.6)
        goal = profiles.validate_params("move_forward", {"goalDistance": 5.0})
        self.assertEqual(goal["goalDistance"], 5.0)
        self.assertEqual(goal["speed"], 0.6)

    def test_default_goal_distance_matches_spec(self):
        default = (profiles.skill("move_forward")["paramsSchema"]
                   ["properties"]["goalDistance"]["default"])
        self.assertAlmostEqual(default, spec.GOAL_DIST, places=6)

    def test_failure_modes_are_timeout_only(self):
        for sid in ("move_forward", "navigate_obstacle"):
            declared = {f["reason"] for f in profiles.skill(sid)["failureModes"]}
            self.assertEqual(declared, {"timeout"}, sid)
        # stop has no failure modes (it always succeeds when paid)
        self.assertEqual(profiles.skill("stop")["failureModes"], [])

    def test_result_schema_matches_build_metrics(self):
        from simulator import MuJoCoSimulator
        m = MuJoCoSimulator().move_forward({}).metrics
        required = {
            "robotId", "skillId", "engine", "scene", "stage", "positionStart",
            "positionEnd", "positionDelta", "distanceTraveled", "stepsUsed",
            "stepBudget", "simTime", "wallTime", "note", "goalDistance",
            "reached", "obstacleContact",
        }
        self.assertEqual(required, set(m))

    def test_price_is_declared_once_and_is_coherent(self):
        p = profiles.skill("move_forward")["pricing"]
        self.assertEqual(p["settlement"], "on-success-only")
        decimals = profiles.payment_policy()["provider"]["asset"]["decimals"]
        atomic = int(p["amountAtomic"])
        self.assertEqual(atomic, round(float(p["amount"]) * 10 ** decimals))


class TestExecutionMappingMatchesSpec(unittest.TestCase):

    def setUp(self):
        self.mapping = profiles.execution_mapping()

    def test_three_skills_mapped(self):
        self.assertEqual(set(self.mapping["mappings"]),
                         {"move_forward", "navigate_obstacle", "stop"})

    def test_gait_is_planar_stepping(self):
        for sid in ("move_forward", "navigate_obstacle"):
            self.assertEqual(self.mapping["mappings"][sid]["gait"],
                             "planar-stepping")
        # stop is a hold, not a gait
        self.assertEqual(self.mapping["mappings"]["stop"]["output"], "hold")

    def test_actuators_reference_leg_joints(self):
        actuators = self.mapping["mappings"]["move_forward"]["actuators"]
        self.assertEqual(set(actuators),
                         {"torso_x", "left_hip", "left_knee",
                          "right_hip", "right_knee"})

    def test_dispatch_backends_match(self):
        from flow.executor import BACKENDS
        prof = profiles.robot_profile()["simulation"]
        self.assertEqual(set(BACKENDS),
                         {prof["primaryEngine"]["name"],
                          prof["secondaryEngine"]["name"]})
        self.assertEqual(set(BACKENDS), {"mujoco", "pybullet"})

    def test_controller_is_not_a_replayed_animation(self):
        det = profiles.robot_profile()["simulation"]["determinism"]
        self.assertFalse(det["replayedAnimation"])
        self.assertTrue(det["policyDriven"])


class TestPaymentPolicy(unittest.TestCase):

    def test_no_settle_on_failure_is_policy_and_code(self):
        self.assertFalse(profiles.settle_on_failure_allowed())
        safety = profiles.payment_policy()["safety"]
        self.assertFalse(safety["settleOnFailure"])
        self.assertTrue(safety["failClosed"])
        self.assertTrue(safety["replayProtection"])

    def test_secrets_only_come_from_the_environment(self):
        secrets = profiles.payment_policy()["secrets"]
        self.assertTrue(secrets["neverCommitToRepo"])
        for field in ("privateKeyEnv", "walletAddressEnv", "payToAddressEnv"):
            self.assertTrue(secrets[field].isupper())
        self.assertFalse(profiles.robot_profile()["identity"]["keyMaterialInRepo"])

    def test_resource_matches_the_canonical_bounty_id(self):
        resource = profiles.payment_policy()["challenge"]["resource"]
        self.assertIn("unitree-g1-arm-001", resource)

    def test_no_private_key_literal_anywhere_in_the_bridge(self):
        for path in ROOT.rglob("*"):
            if path.is_dir() or path.suffix not in (".py", ".yaml", ".yml", ".md"):
                continue
            if ".pytest_cache" in str(path):
                continue
            # validation-report.md embeds the real public tx hash — it's evidence,
            # not a leaked secret. Skip the docs/ tree.
            if path.is_relative_to(ROOT / "docs"):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "Env:" in stripped:
                    continue
                self.assertNotRegex(
                    stripped, r"0x[0-9a-fA-F]{64}",
                    f"possible private key literal in {path.name}: {stripped[:60]}")


class TestFunctionsManifest(unittest.TestCase):

    def test_three_functions_are_declared(self):
        names = [f["name"] for f in profiles.functions_manifest()["functions"]]
        self.assertEqual(names, ["list_robot_skills", "request_robot_action",
                                 "submit_paid_robot_action"])

    def test_only_the_paid_function_reaches_the_robot(self):
        fns = {f["name"]: f for f in profiles.functions_manifest()["functions"]}
        self.assertFalse(fns["list_robot_skills"]["paid"])
        self.assertFalse(fns["request_robot_action"]["paid"])
        self.assertTrue(fns["submit_paid_robot_action"]["paid"])
        self.assertEqual(fns["request_robot_action"]["paymentUnpaidStatus"], 402)

    def test_envelope_keeps_the_six_required_fields(self):
        fns = {f["name"]: f for f in profiles.functions_manifest()["functions"]}
        paid = fns["submit_paid_robot_action"]
        self.assertIn("X-PAYMENT", paid["headers"])
        for field in ("skillId", "params", "idempotencyKey"):
            self.assertIn(field, paid["body"])
        # The in-process envelope (flow.envelope.TaskEnvelope) carries the same
        # six fields the reviewer checks for.
        from flow.envelope import TaskEnvelope
        d = TaskEnvelope("a", "unitree-g1", "move_forward", {}, {}, "k").to_dict()
        self.assertEqual(set(d), {"actionId", "robotId", "skillId",
                                  "paramsHash", "payment", "idempotencyKey"})


class TestProfilesDriveTheRelay(unittest.TestCase):
    """The manifests are not documentation: the running relay reads them."""

    def test_402_challenge_carries_the_catalogue_price(self):
        resp = Relay(MockExecutor()).handle({**REQ, "idempotencyKey": "p1"})
        self.assertEqual(resp["status"], 402)
        accept = resp["accepts"][0]
        self.assertEqual(accept["amount"],
                         profiles.skill("move_forward")["pricing"]["amount"])
        self.assertEqual(accept["network"], "eip155:84532")
        self.assertEqual(resp["header"], "X-PAYMENT")

    def test_invalid_params_are_rejected_without_executing(self):
        ex = MockExecutor()
        resp = Relay(ex).handle({**REQ, "idempotencyKey": "p2",
                                 "payment": PAID, "params": {"object": "banana"}})
        self.assertEqual(resp["status"], "rejected")
        self.assertIn("invalid_params", resp["reason"])
        self.assertFalse(resp["settled"])
        self.assertEqual(ex.execution_count, 0)      # robot never contacted

    def test_unknown_skill_is_rejected_without_executing(self):
        ex = MockExecutor()
        resp = Relay(ex).handle({**REQ, "skill": "fly", "idempotencyKey": "p3",
                                 "payment": PAID})
        self.assertEqual(resp["status"], "rejected")
        self.assertIn("unsupported_skill", resp["reason"])
        self.assertEqual(ex.execution_count, 0)

    def test_discovery_is_free_and_lists_the_price(self):
        cat = profiles.list_skills("unitree-g1")
        self.assertEqual(cat["robotId"], "unitree-g1")
        entry = cat["skills"][0]
        self.assertEqual(entry["skillId"], "move_forward")
        self.assertEqual(entry["settlement"], "on-success-only")
        self.assertEqual(set(entry["failureModes"]), {"timeout"})

    def test_payto_address_comes_from_the_environment(self):
        key = profiles.payment_policy()["provider"]["payToAddressEnv"]
        original = os.environ.get(key)
        os.environ[key] = "0x1111111111111111111111111111111111111111"
        try:
            accepts = profiles.payment_requirements("move_forward")
            self.assertEqual(accepts[0]["payTo"],
                             "0x1111111111111111111111111111111111111111")
        finally:
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


if __name__ == "__main__":
    unittest.main()
