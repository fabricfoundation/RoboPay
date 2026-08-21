"""Keep the Atlas profile's registry documents and public catalog in lockstep."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
PROFILE = (
    ROOT
    / "registry/vendors/boston-dynamics/atlas"
    / "boston-dynamics.atlas-drc.mujoco-webots-wave.v1"
)
REQUIRED = (
    "robot.profile.yaml",
    "skills.yaml",
    "functions.yaml",
    "payment-policy.yaml",
    "execution-mapping.yaml",
    "skill-catalog.json",
    "examples/action-envelope.wave_right_arm.json",
    "examples/action-envelope.stop.json",
    "tests/skill-contract.test.yaml",
    "docs/README.md",
    "docs/validation-report.md",
    "docs/evidence/evidence-manifest.yaml",
)


def _yaml(name: str) -> dict:
    document = yaml.safe_load((PROFILE / name).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError(f"{name} must contain a mapping")
    return document


class AtlasRegistryContractTests(unittest.TestCase):
    def test_profile_is_complete_and_cross_document_ids_match(self) -> None:
        for name in REQUIRED:
            self.assertTrue((PROFILE / name).is_file(), name)
        profile = _yaml("robot.profile.yaml")
        profile_id = profile["profileId"]
        self.assertEqual(PROFILE.name, profile_id)
        self.assertEqual(profile["simulation"]["scope"], "simulator-only")
        self.assertEqual(profile["simulation"]["primaryEngine"], "MuJoCo 3.3.0")
        self.assertEqual(profile["simulation"]["validationEngine"], "Webots R2025a")
        self.assertIn("current electric Atlas", profile["simulation"]["model"]["limitation"])
        self.assertIn("30 movable one-DoF joints", profile["simulation"]["model"]["limitation"])
        self.assertIn("56 DoF", profile["simulation"]["model"]["limitation"])
        documents = {
            name: _yaml(name)
            for name in REQUIRED
            if name.endswith(".yaml") and name.count("/") == 0
        }
        for name, document in documents.items():
            if name != "payment-policy.yaml":
                self.assertEqual(document.get("profileId"), profile_id, name)

        skills = documents["skills.yaml"]["skills"]
        skill_ids = {item["skillId"] for item in skills}
        catalog = json.loads((PROFILE / "skill-catalog.json").read_text(encoding="utf-8"))
        self.assertEqual({item["skill_id"] for item in catalog}, skill_ids)
        policies = documents["payment-policy.yaml"]["policies"]
        self.assertEqual({item["skillId"] for item in policies}, skill_ids)
        self.assertEqual(set(documents["execution-mapping.yaml"]["mappings"]), skill_ids)

    def test_transport_payment_and_wave_bounds_match_runtime_contract(self) -> None:
        profile = _yaml("robot.profile.yaml")
        mapping = _yaml("execution-mapping.yaml")
        runtime = profile["runtime"]
        transport = mapping["transport"]
        self.assertEqual(runtime["transport"], transport["type"])
        for field in ("actionTopic", "resultTopic", "metricsTopic"):
            self.assertEqual(runtime[field], transport[field])
        self.assertEqual(runtime["readyTopic"], "robot/boston_dynamics_atlas_drc/ready")

        skills = {item["skillId"]: item for item in _yaml("skills.yaml")["skills"]}
        catalog = {
            item["skill_id"]: item
            for item in json.loads((PROFILE / "skill-catalog.json").read_text(encoding="utf-8"))
        }
        limits = mapping["mappings"]["wave_right_arm"]["limits"]
        self.assertEqual(skills["wave_right_arm"]["params"]["maxDurationSec"]["min"], 5)
        self.assertEqual(catalog["wave_right_arm"]["params"]["maxDurationSec"]["minimum"], 5)
        self.assertEqual(limits["maxDurationSec"]["min"], 5)
        self.assertTrue(all(item["required"] for item in _yaml("payment-policy.yaml")["policies"]))
        settlement_rule = _yaml("payment-policy.yaml")["settlement"]["rule"]
        self.assertIn("paid stop request", settlement_rule)
        self.assertIn("correlated safe-stop success", settlement_rule)

        function_names = {item["name"] for item in _yaml("functions.yaml")["functions"]}
        self.assertEqual(
            function_names,
            {
                "get_robot_profile",
                "list_robot_skills",
                "request_robot_action",
                "submit_paid_robot_action",
                "get_action_status",
            },
        )

    def test_evidence_manifest_does_not_overclaim_uncaptured_proof(self) -> None:
        evidence = _yaml("docs/evidence/evidence-manifest.yaml")
        self.assertEqual(evidence["profileId"], PROFILE.name)
        self.assertIn("electric Atlas", evidence["claimBoundary"]["notClaimed"])
        self.assertIn("joint-level compatibility", evidence["claimBoundary"]["notClaimed"])
        by_id = {item["evidenceId"]: item for item in evidence["evidence"]}
        self.assertEqual(
            by_id["ATLAS-DRC-LIVE-X402-CURRENT-HEAD"]["status"],
            "pending-operator-capture",
        )
        self.assertEqual(
            by_id["ATLAS-DRC-CONTINUOUS-VISUAL-CURRENT-HEAD"]["status"],
            "pending-operator-capture",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
