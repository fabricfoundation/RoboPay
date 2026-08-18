"""Registry contract tests — validates YAML/JSON manifests against bridge constants."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from bridge.boston_dynamics.atlas_mujoco_bridge.bridge import PROFILE_ID, ROBOT_ID, ALLOWED_ACTIONS
from bridge.boston_dynamics.atlas_mujoco_bridge.control_core import POLICY_ID, ACTUATOR_ORDER

REGISTRY_DIR = Path(__file__).resolve().parents[4] / "registry" / "boston_dynamics" / "atlas" / PROFILE_ID


def _load_yaml(name: str) -> dict:
    path = REGISTRY_DIR / name
    assert path.exists(), f"Missing registry file: {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(name: str) -> dict:
    path = REGISTRY_DIR / name
    assert path.exists(), f"Missing registry file: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_profile_matches_bridge():
    profile = _load_yaml("robot.profile.yaml")
    assert profile["profileId"] == PROFILE_ID
    assert profile["vendor"] == "boston_dynamics"
    assert "robotModel" in profile


def test_skills_define_allowed_actions():
    skills = _load_yaml("skills.yaml")
    skill_ids = {s["skillId"] for s in skills["skills"]}
    assert skill_ids == ALLOWED_ACTIONS


def test_skill_catalog_matches_skills():
    skills = _load_yaml("skills.yaml")
    catalog = _load_json("skill-catalog.json")
    skill_ids = {s["skillId"] for s in skills["skills"]}
    catalog_ids = {s["skillId"] for s in catalog["skills"]}
    assert skill_ids == catalog_ids


def test_payment_policy_references_profile():
    pp = _load_yaml("payment-policy.yaml")
    assert pp["profileId"] == PROFILE_ID
    assert "network" in pp
    assert "skills" in pp


def test_execution_mapping_topics():
    em = _load_yaml("execution-mapping.yaml")
    assert "transport" in em
    assert "actionTopic" in em["transport"]
    assert em["profileId"] == PROFILE_ID


def test_actuator_count_in_registry():
    skills = _load_yaml("skills.yaml")
    for skill in skills["skills"]:
        if "actuators" in skill.get("movementLimits", {}):
            assert skill["movementLimits"]["actuators"] == len(ACTUATOR_ORDER)
