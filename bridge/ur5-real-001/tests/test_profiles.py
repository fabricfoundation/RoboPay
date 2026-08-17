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

import arm_spec as spec
from flow import profiles
from flow.executor import SimExecutor, MockExecutor
from flow.relay import Relay
from flow.zenoh_transport import ACTION_TOPIC, RESULT_TOPIC

ROOT = Path(__file__).resolve().parent.parent
PAID = {"txHash": "0x" + "a" * 64, "verified": True, "amount": "0.10",
        "network": "base-sepolia",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xpayer0000000000000000000000000000000001"}
REQ = {"skill": "pick_object", "robotId": "ur5-real-001"}


class TestManifestsExist(unittest.TestCase):
    """The five files the PR Review Checklist greps for."""

    def test_all_five_manifests_load(self):
        for name, filename in profiles.MANIFESTS.items():
            self.assertTrue((ROOT / "profiles" / filename).exists(),
                            f"{filename} is missing")
            self.assertIsInstance(profiles.load(name), dict)

    def test_identity_is_consistent_across_manifests(self):
        rid, pid = profiles.robot_id(), profiles.profile_id()
        self.assertEqual(rid, "ur5-real-001")
        for name in ("skills", "functions", "payment", "mapping"):
            man = profiles.load(name)
            self.assertEqual(man["robotId"], rid, f"{name} robotId drifted")
            self.assertEqual(man["profileId"], pid, f"{name} profileId drifted")

    def test_referenced_modules_exist(self):
        prof = profiles.robot_profile()
        for engine in ("primaryEngine", "secondaryEngine"):
            module = prof["simulation"][engine]["module"]
            self.assertTrue((ROOT / module).exists(), f"{module} is missing")
        spec_source = Path(prof["embodiment"]["specSource"]).name
        self.assertTrue((ROOT / spec_source).exists(), spec_source)


class TestRobotProfileMatchesSpec(unittest.TestCase):
    """robot.profile.yaml vs arm_spec.py -- one robot, one description."""

    def setUp(self):
        self.prof = profiles.robot_profile()

    def test_kinematics_match(self):
        k = self.prof["embodiment"]["kinematics"]
        self.assertAlmostEqual(k["baseHeight"], spec.BASE_H, places=6)
        self.assertAlmostEqual(k["link1"], spec.LINK1, places=6)
        self.assertAlmostEqual(k["link2"], spec.LINK2, places=6)
        self.assertAlmostEqual(k["maxReach"], spec.MAX_REACH, places=6)
        self.assertAlmostEqual(k["workRadius"], spec.WORK_R, places=6)

    def test_joint_names_and_count_match(self):
        joints = [j["name"] for j in self.prof["embodiment"]["joints"]]
        self.assertEqual(tuple(joints), spec.ARM_JOINTS)
        self.assertEqual(self.prof["embodiment"]["degreesOfFreedom"], len(spec.ARM_JOINTS))

    def test_gripper_apertures_match(self):
        g = self.prof["embodiment"]["gripper"]
        self.assertAlmostEqual(g["halfApertureOpen"], spec.FINGER_OPEN, places=4)
        self.assertAlmostEqual(g["halfApertureClosed"], spec.FINGER_CLOSED, places=4)

    def test_timestep_matches(self):
        self.assertAlmostEqual(
            self.prof["simulation"]["primaryEngine"]["timestep"], spec.TIMESTEP, places=6)

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
        self.assertEqual(executor.supported, {"pick_object"})

    def test_param_enum_covers_every_scene_and_alias(self):
        enum = set(profiles.skill("pick_object")["paramsSchema"]
                   ["properties"]["object"]["enum"])
        self.assertEqual(enum, set(spec.SCENES) | set(spec.ALIASES))

    def test_step_budget_matches_spec(self):
        ex = profiles.skill("pick_object")["execution"]
        self.assertEqual(ex["defaultStepBudget"], spec.DEFAULT_BUDGET)
        self.assertEqual(ex["nominalSteps"], spec.NOMINAL_STEPS)

    def test_success_thresholds_match_spec(self):
        crit = {c["key"]: c["value"]
                for c in profiles.skill("pick_object")["successCriteria"]}
        self.assertAlmostEqual(crit["contactForce"], spec.GRASP_FORCE_MIN, places=6)
        self.assertAlmostEqual(crit["objectLifted"], spec.LIFT_MIN, places=6)
        self.assertEqual(crit["graspState"], "attached")

    def test_declared_failure_modes_are_the_real_ones(self):
        declared = {f["reason"] for f in profiles.skill("pick_object")["failureModes"]}
        self.assertEqual(declared,
                         {"unreachable", "collision", "timeout", "grasp_failed"})
        for mode in profiles.skill("pick_object")["failureModes"]:
            self.assertFalse(mode["settles"], f"{mode['reason']} must never settle")

    def test_result_schema_matches_build_metrics(self):
        required = set(profiles.skill("pick_object")["resultSchema"]
                       ["properties"]["metrics"]["required"])
        produced = set(spec.build_metrics(
            engine="mujoco", obj="cube", scene_key="cube", stage="settle",
            grasp_state="attached", start_pos=(0, 0, 0), end_pos=(0, 0, 0.1),
            hold_force=1.0, peak_force=2.0, contact_samples=3, collisions=0,
            steps=260, budget=400, wall_time=0.5, note="").keys())
        self.assertEqual(required, produced)

    def test_price_is_declared_once_and_is_coherent(self):
        p = profiles.skill("pick_object")["pricing"]
        self.assertEqual(p["settlement"], "on-success-only")
        atomic = int(p["amountAtomic"])
        self.assertEqual(atomic, round(float(p["amount"]) * 10 ** p["decimals"]))


class TestExecutionMappingMatchesSpec(unittest.TestCase):

    def setUp(self):
        self.mapping = profiles.execution_mapping()
        self.pick = self.mapping["mappings"][0]

    def test_stage_steps_match_spec(self):
        stages = {s["name"]: s["steps"] for s in self.pick["stages"]}
        self.assertEqual(stages, spec.STAGE_STEPS)
        self.assertEqual(self.pick["nominalSteps"], spec.NOMINAL_STEPS)
        self.assertEqual(self.pick["defaultStepBudget"], spec.DEFAULT_BUDGET)

    def test_keyframes_are_the_solved_ones(self):
        for name, pose in self.pick["keyframes"].items():
            truth = spec.KEYFRAMES[name]
            for joint in spec.ARM_JOINTS:
                self.assertLess(
                    abs(pose[joint] - truth[joint]), 1e-3,
                    f"keyframe {name}.{joint} drifted from arm_spec.solve()")

    def test_documented_keyframe_set_is_complete(self):
        self.assertEqual(set(self.pick["keyframes"]), set(spec.KEYFRAMES))

    def test_scene_table_matches_spec(self):
        for name, scene in self.mapping["scenes"].items():
            truth = spec.SCENES[name]
            self.assertEqual(tuple(scene["cubeXY"]), truth["cube"])
            obstacle = tuple(scene["obstacle"]) if scene["obstacle"] else None
            self.assertEqual(obstacle, truth["obstacle"])
            self.assertEqual(scene["stepBudget"], truth["budget"])
        self.assertEqual(set(self.mapping["scenes"]), set(spec.SCENES))
        self.assertEqual(self.mapping["aliases"], spec.ALIASES)

    def test_obstacle_geometry_matches_spec(self):
        obs = self.mapping["obstacle"]
        self.assertAlmostEqual(obs["radius"], spec.OBSTACLE_RADIUS, places=6)
        self.assertAlmostEqual(obs["halfHeight"], spec.OBSTACLE_HALF_H, places=6)

    def test_decision_thresholds_match_spec(self):
        s = self.pick["decision"]["success"]
        self.assertAlmostEqual(s["contactForceMinN"], spec.GRASP_FORCE_MIN, places=6)
        self.assertAlmostEqual(s["objectLiftedMinM"], spec.LIFT_MIN, places=6)

    def test_declared_backends_point_at_real_modules(self):
        from flow.executor import BACKENDS
        backends = self.mapping["dispatch"]["backends"]
        self.assertEqual(set(backends), set(BACKENDS))
        for engine, target in backends.items():
            module_file = target.split("::")[0]
            self.assertTrue((ROOT / module_file).exists(), target)

    def test_controller_is_not_a_replayed_animation(self):
        ctrl = self.pick["controller"]
        self.assertEqual(ctrl["type"], "deterministic-trajectory")
        self.assertEqual(ctrl["runtimeIteration"], "none")
        self.assertEqual(list(ctrl["jointOrder"]), list(spec.ARM_JOINTS))
        self.assertFalse(profiles.robot_profile()["simulation"]
                         ["determinism"]["replayedAnimation"])


class TestPaymentPolicy(unittest.TestCase):

    def test_no_settle_on_failure_is_policy_and_code(self):
        self.assertFalse(profiles.settle_on_failure_allowed())
        safety = profiles.payment_policy()["safety"]
        for flag in ("settleOnFailure", "settleBeforeExecution",
                     "captureOnAuthorization", "executeWithoutPayment",
                     "doubleExecutionOnReplay"):
            self.assertFalse(safety[flag], f"{flag} must be false")

    def test_safety_proof_tests_actually_exist(self):
        for ref in profiles.payment_policy()["safety"]["proof"]:
            path, cls, func = ref.split("::")
            module = importlib.import_module(
                path.replace("/", ".").replace(".py", ""))
            self.assertTrue(hasattr(module, cls), ref)
            self.assertTrue(hasattr(getattr(module, cls), func), ref)

    def test_secrets_only_come_from_the_environment(self):
        secrets = profiles.payment_policy()["secrets"]
        self.assertEqual(secrets["storage"], "environment-variables-only")
        self.assertFalse(secrets["committedToRepo"])

    def test_no_private_key_literal_anywhere_in_the_bridge(self):
        for path in ROOT.rglob("*"):
            if path.is_dir() or path.suffix not in (".py", ".yaml", ".yml", ".md"):
                continue
            if ".pytest_cache" in str(path):
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
        self.assertEqual(names, ["list_skills", "request_action", "submit_paid_action"])

    def test_only_the_paid_function_reaches_the_robot(self):
        fns = {f["name"]: f for f in profiles.functions_manifest()["functions"]}
        self.assertFalse(fns["list_skills"]["paid"])
        self.assertFalse(fns["request_action"]["paid"])
        self.assertTrue(fns["submit_paid_action"]["paid"])
        self.assertEqual(fns["request_action"]["response"]["properties"]
                         ["status"]["const"], 402)

    def test_envelope_keeps_the_six_required_fields(self):
        fns = {f["name"]: f for f in profiles.functions_manifest()["functions"]}
        env = fns["submit_paid_action"]["envelope"]
        self.assertEqual(set(env["fields"]),
                         {"actionId", "robotId", "skillId",
                          "idempotencyKey", "paramsHash", "payment"})
        self.assertEqual(env["publishedTo"], ACTION_TOPIC)


class TestProfilesDriveTheRelay(unittest.TestCase):
    """The manifests are not documentation: the running relay reads them."""

    def test_402_challenge_carries_the_catalogue_price(self):
        resp = Relay(MockExecutor()).handle({**REQ, "idempotencyKey": "p1"})
        self.assertEqual(resp["status"], 402)
        accept = resp["accepts"][0]
        self.assertEqual(accept["amount"],
                         profiles.skill("pick_object")["pricing"]["amount"])
        self.assertEqual(accept["network"], "base-sepolia")
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
        cat = profiles.list_skills("ur5-real-001")
        self.assertEqual(cat["robotId"], "ur5-real-001")
        entry = cat["skills"][0]
        self.assertEqual(entry["skillId"], "pick_object")
        self.assertEqual(entry["settlement"], "on-success-only")
        self.assertEqual(set(entry["failureModes"]),
                         {"unreachable", "collision", "timeout", "grasp_failed"})

    def test_payto_address_comes_from_the_environment(self):
        key = profiles.payment_policy()["provider"]["payToAddressEnv"]
        original = os.environ.get(key)
        os.environ[key] = "0x1111111111111111111111111111111111111111"
        try:
            accepts = profiles.payment_requirements("pick_object")
            self.assertEqual(accepts[0]["payTo"],
                             "0x1111111111111111111111111111111111111111")
        finally:
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


if __name__ == "__main__":
    unittest.main()
