"""Structural validation for the registry profile YAML files -- catches
drift between robot.profile.yaml, skills.yaml, execution-mapping.yaml,
payment-policy.yaml, and functions.yaml before it reaches review."""
import os

import pytest
import yaml

PROFILE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
PROFILE_ID = "booster.k1.obstacle-nav-sim.v1"
SKILL_ID = "k1_navigate_avoid_obstacles"


def load(filename):
    path = os.path.join(PROFILE_DIR, filename)
    with open(path) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def profile():
    return load("robot.profile.yaml")


@pytest.fixture(scope="module")
def skills():
    return load("skills.yaml")


@pytest.fixture(scope="module")
def execution_mapping():
    return load("execution-mapping.yaml")


@pytest.fixture(scope="module")
def payment_policy():
    return load("payment-policy.yaml")


@pytest.fixture(scope="module")
def functions():
    return load("functions.yaml")


def test_all_yaml_files_present_and_parse():
    for filename in ("robot.profile.yaml", "skills.yaml", "execution-mapping.yaml",
                      "payment-policy.yaml", "functions.yaml"):
        data = load(filename)
        assert data is not None, f"{filename} parsed to None (empty file?)"


def test_profile_has_required_top_level_fields(profile):
    for field in ("schemaVersion", "vendor", "robotModel", "profileId",
                  "profileVersion", "runtime", "identity", "maintainers", "status"):
        assert field in profile, f"robot.profile.yaml missing field: {field}"


def test_profile_id_consistent_across_all_files(profile, skills, execution_mapping, payment_policy, functions):
    assert profile["profileId"] == PROFILE_ID
    assert skills["profileId"] == PROFILE_ID
    assert execution_mapping["profileId"] == PROFILE_ID
    assert payment_policy["profileId"] == PROFILE_ID
    assert functions["profileId"] == PROFILE_ID


def test_profile_declares_simulation_scope(profile):
    assert profile["submission"]["scope"] == "simulation"
    assert "mujoco" in profile["submission"]["simulators"]
    assert "webots" in profile["submission"]["simulators"]


def test_profile_runtime_matches_bridge_topics(profile):
    runtime = profile["runtime"]
    assert runtime["transport"] == "zenoh"
    assert runtime["actionTopic"] == "robot/tunnel/action"
    assert runtime["resultTopic"] == "robot/tunnel/result"


def test_skill_id_consistent_across_skills_and_execution_mapping(skills, execution_mapping):
    skill_ids_in_skills = {s["skillId"] for s in skills["skills"]}
    assert SKILL_ID in skill_ids_in_skills

    assert SKILL_ID in execution_mapping["mappings"], \
        "skill defined in skills.yaml has no corresponding execution-mapping.yaml entry"


def test_skill_id_consistent_in_payment_policy(payment_policy):
    skill_ids_in_policies = {p["skillId"] for p in payment_policy["policies"]}
    assert SKILL_ID in skill_ids_in_policies, \
        "skill has no payment policy -- it would be executable without a price"


def test_every_skill_requires_payment(skills):
    for skill in skills["skills"]:
        assert skill.get("paymentRequired") is True, \
            f"skill {skill['skillId']} does not require payment"


def test_execution_mapping_topics_match_profile(profile, execution_mapping):
    assert execution_mapping["transport"]["actionTopic"] == profile["runtime"]["actionTopic"]
    assert execution_mapping["transport"]["resultTopic"] == profile["runtime"]["resultTopic"]


def test_execution_mapping_envelope_matches_tunnel_action_event_schema(execution_mapping):
    """Keeps execution-mapping.yaml's documented envelope contract in sync
    with what the Go tunnel actually publishes to actionTopic
    (tunnel/internal/handlers/handlers.go::PostAction) and what
    bridge/booster_k1_zenoh_bridge.py actually parses (via the shared
    action_event.py). Payment fields are deliberately absent here --
    payment is fully handled in the tunnel before publish; see
    payment-policy.yaml's enforcedBy pointers instead."""
    required = set(execution_mapping["envelope"]["requiredFields"])
    expected = {"actionId", "action", "params"}
    assert required == expected
    assert execution_mapping["envelope"]["correlationField"] == "actionId"


def test_payment_policy_settlement_gate_excludes_success(payment_policy):
    """The one status that must NEVER appear in noSettleResultStatuses
    is 'success' -- otherwise nothing could ever get paid."""
    for policy in payment_policy["policies"]:
        no_settle = policy["settlement"]["noSettleResultStatuses"]
        assert "success" not in no_settle
        assert policy["settlement"]["eligibleOnlyAfterResultStatus"] == "success"


def test_functions_reference_correct_skill_discovery_and_action_endpoints(functions):
    function_names = {f["name"] for f in functions["functions"]}
    assert {"list_robot_skills", "request_robot_action",
            "submit_paid_robot_action", "get_robot_action_status"} <= function_names
