from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from download_vendor_assets import ASSETS, CONTROL_COMMIT, DESCRIPTION_COMMIT
from limx_tron2_sim.contracts import NAVIGATION_SKILL, PROFILE_ID, ROBOT_ID, STOP_SKILL


ROOT = Path(__file__).resolve().parents[1]


def _yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_registry_and_payment_policy_do_not_drift() -> None:
    profile = _yaml("robot.profile.yaml")
    skills = _yaml("skills.yaml")
    policy = _yaml("payment-policy.yaml")
    mapping = _yaml("execution-mapping.yaml")
    catalog = json.loads((ROOT / "skill-catalog.json").read_text(encoding="utf-8"))
    skill_ids = {entry["skillId"] for entry in skills["skills"]}
    assert profile["profileId"] == PROFILE_ID
    assert profile["robotId"] == ROBOT_ID
    assert skills["profileId"] == PROFILE_ID
    assert policy["profileId"] == PROFILE_ID
    assert mapping["profileId"] == PROFILE_ID
    assert policy["network"] == "eip155:84532"
    assert policy["payTo"] == "<configure ROBO_PAYEE_ADDRESS in the Tunnel deployment>"
    assert {NAVIGATION_SKILL, STOP_SKILL} == skill_ids
    assert {entry["skill_id"] for entry in catalog} == skill_ids
    assert {entry["skillId"] for entry in policy["policies"]} == skill_ids
    assert set(mapping["mappings"]) == skill_ids
    assert {entry["priceUSDC"] for entry in policy["policies"]} == {"0.001"}
    assert {entry["price_usdc"] for entry in catalog} == {"0.001"}
    assert profile["modelIdentity"]["source"] == "https://github.com/limxdynamics/tron2-robot-description"
    assert mapping["model"]["source"] == profile["modelIdentity"]["source"]
    assert DESCRIPTION_COMMIT == profile["modelIdentity"]["sourceCommit"]
    assert CONTROL_COMMIT == profile["controlIdentity"]["sourceCommit"]
    identities = profile["modelIdentity"] | {
        "policy": profile["controlIdentity"]["policy"],
        "policySha256": profile["controlIdentity"]["policySha256"],
        "encoder": profile["controlIdentity"]["encoder"],
        "encoderSha256": profile["controlIdentity"]["encoderSha256"],
        "parameters": profile["controlIdentity"]["parameters"],
        "parametersSha256": profile["controlIdentity"]["parametersSha256"],
    }
    for path_key, hash_key in (
        ("urdf", "urdfSha256"),
        ("mjcf", "mjcfSha256"),
        ("policy", "policySha256"),
        ("encoder", "encoderSha256"),
        ("parameters", "parametersSha256"),
    ):
        source = ROOT / identities[path_key]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == identities[hash_key]
    assert len(profile["modelIdentity"]["meshes"]) == 11
    for mesh in profile["modelIdentity"]["meshes"]:
        source = ROOT / mesh["path"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == mesh["sha256"]

    declared_assets = {
        identities["urdf"]: identities["urdfSha256"],
        identities["mjcf"]: identities["mjcfSha256"],
        identities["policy"]: identities["policySha256"],
        identities["encoder"]: identities["encoderSha256"],
        identities["parameters"]: identities["parametersSha256"],
        **{mesh["path"]: mesh["sha256"] for mesh in profile["modelIdentity"]["meshes"]},
    }
    assert {asset.destination: asset.sha256 for asset in ASSETS} == declared_assets

    assert mapping["model"]["urdf"] == profile["modelIdentity"]["urdf"]
    assert mapping["model"]["mjcf"] == profile["modelIdentity"]["mjcf"]
    assert mapping["model"]["urdfSha256"] == profile["modelIdentity"]["urdfSha256"]
    assert mapping["model"]["commit"] == profile["modelIdentity"]["sourceCommit"]
    assert mapping["control"]["commit"] == profile["controlIdentity"]["sourceCommit"]
    assert mapping["control"]["policy"] == profile["controlIdentity"]["policy"]
    assert mapping["control"]["encoder"] == profile["controlIdentity"]["encoder"]
    assert mapping["control"]["parameters"] == profile["controlIdentity"]["parameters"]


def test_profile_docs_and_public_action_examples_are_present() -> None:
    """Keep the reviewer-facing profile material coupled to the registry."""
    docs = ROOT / "docs"
    assert (docs / "README.md").is_file()
    assert (docs / "validation-report.md").is_file()
    assert (docs / "evidence" / "evidence-manifest.yaml").is_file()
    assert (ROOT / "tests" / "skill-contract.test.yaml").is_file()
    for legal_file in (
        "vendor/limx-tron2/robot-description/LICENSE",
        "vendor/limx-tron2/robot-description/NOTICE",
        "vendor/limx-tron2/robot-description/THIRD_PARTY_NOTICES.md",
        "vendor/limxdynamics/tron2_rl_deploy_python/LICENSE",
        "vendor/limxdynamics/tron2_rl_deploy_python/NOTICE",
        "vendor/limxdynamics/tron2_rl_deploy_python/THIRD_PARTY_NOTICES.md",
        "vendor/limxdynamics/tron2_rl_deploy_python/MODEL_CARD.md",
    ):
        assert (ROOT / legal_file).is_file()

    inspect_example = json.loads(
        (ROOT / "examples" / "action-envelope.navigate_obstacle_course.json").read_text(
            encoding="utf-8"
        )
    )
    stop_example = json.loads(
        (ROOT / "examples" / "action-envelope.stop.json").read_text(encoding="utf-8")
    )
    for example, skill_id in ((inspect_example, NAVIGATION_SKILL), (stop_example, STOP_SKILL)):
        assert example["skillId"] == skill_id
        assert example["robotId"] == ROBOT_ID
        assert example["actionId"]
        assert example["idempotencyKey"]
        assert example["params"] == {}


def test_webots_controller_is_task_driven_without_pose_writes_or_replay() -> None:
    controller = (
        ROOT
        / "simulators/webots/controllers/limx_tron2_obstacle_controller/limx_tron2_obstacle_controller.py"
    ).read_text(encoding="utf-8")
    world = (ROOT / "simulators/webots/scenes/tron2_obstacle_course_template.wbt").read_text(
        encoding="utf-8"
    )

    assert "RoutePlanner" in controller
    assert "self_node.setVelocity(command)" in controller
    assert '"supervisor_root_pose_writes": 0' in controller
    assert '"supervisor_velocity_commands"' in controller
    assert '"trajectory_replay": False' in controller
    assert '"waypoints_completed"' in controller
    assert '"collision"' in controller
    assert "basicTimeStep 2" in world
    for forbidden_root_write in (
        "setSFVec3f",
        "setSFRotation",
        "resetPhysics",
        "simulationReset",
    ):
        assert forbidden_root_write not in controller


def test_sim2sim_validation_level_is_documented_without_drift() -> None:
    mapping = _yaml("execution-mapping.yaml")
    evidence = _yaml("docs/evidence/evidence-manifest.yaml")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    report = (ROOT / "docs/validation-report.md").read_text(encoding="utf-8")

    validation = mapping["validation"]
    assert validation["mujoco"]["level"] == "actuator-level"
    assert validation["webots"]["level"] == "task-level"
    assert validation["webots"]["rootPoseWrites"] == 0
    assert validation["webots"]["trajectoryReplay"] is False
    assert validation["webots"]["builtInDemoMotion"] is False

    levels = {entry.get("validationLevel") for entry in evidence["evidence"]}
    assert {"actuator-level", "task-level-sim-to-sim"} <= levels
    for document in (readme, report):
        assert "task-level" in document
        assert "actuator-level" in document
        assert "already-running" in document
