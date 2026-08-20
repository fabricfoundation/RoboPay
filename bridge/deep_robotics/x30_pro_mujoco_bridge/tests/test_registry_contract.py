"""Keep the X30 registry profile and Tunnel catalog in a single contract."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from x30_pro_mujoco_bridge.contracts import DRIVE_SKILL, ROBOT_ID, STOP_SKILL, validate_action


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
PROFILE = (
    ROOT
    / "registry/vendors/deep-robotics/x30-pro"
    / "deep-robotics.x30-pro.mujoco-webots-inspection.v1"
)
REQUIRED = (
    "robot.profile.yaml",
    "skills.yaml",
    "functions.yaml",
    "payment-policy.yaml",
    "execution-mapping.yaml",
    "skill-catalog.json",
)


def _yaml(name: str) -> dict:
    document = yaml.safe_load((PROFILE / name).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError(f"{name} must contain a mapping")
    return document


class X30RegistryContractTests(unittest.TestCase):
    def test_profile_is_complete_and_cross_document_ids_match(self) -> None:
        for name in REQUIRED:
            self.assertTrue((PROFILE / name).is_file(), name)
        profile = _yaml("robot.profile.yaml")
        profile_id = profile["profileId"]
        self.assertEqual(PROFILE.name, profile_id)
        documents = {name: _yaml(name) for name in REQUIRED if name.endswith(".yaml")}
        for name, document in documents.items():
            if name != "payment-policy.yaml":
                self.assertEqual(document.get("profileId"), profile_id, name)

        skill_ids = {item["skillId"] for item in documents["skills.yaml"]["skills"]}
        catalog = json.loads((PROFILE / "skill-catalog.json").read_text(encoding="utf-8"))
        self.assertEqual({item["skill_id"] for item in catalog}, skill_ids)
        policies = documents["payment-policy.yaml"]["policies"]
        self.assertEqual({item["skillId"] for item in policies}, skill_ids)
        self.assertEqual(set(documents["execution-mapping.yaml"]["skills"]), skill_ids)

    def test_fixed_route_matches_the_fail_closed_runtime_contract(self) -> None:
        skills = {item["skillId"]: item for item in _yaml("skills.yaml")["skills"]}
        catalog = {
            item["skill_id"]: item
            for item in json.loads((PROFILE / "skill-catalog.json").read_text(encoding="utf-8"))
        }
        request = validate_action(
            ROBOT_ID,
            DRIVE_SKILL,
            DRIVE_SKILL,
            {},
        )
        self.assertEqual(request.skill_id, DRIVE_SKILL)
        self.assertEqual(skills[DRIVE_SKILL]["parameters"], {})
        self.assertEqual(catalog[DRIVE_SKILL]["params"], {})
        self.assertEqual(request.gait_cycles, 34)
        self.assertEqual(request.hip_sweep_rad, 0.10)
        self.assertEqual(request.max_duration_sec, 45.0)
        self.assertEqual(validate_action(ROBOT_ID, STOP_SKILL, STOP_SKILL, {}).skill_id, STOP_SKILL)
        self.assertTrue(all(item["required"] for item in _yaml("payment-policy.yaml")["policies"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
