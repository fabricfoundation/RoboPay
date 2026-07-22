from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

import yaml


PROFILE = Path(__file__).resolve().parents[1]
PUBLIC_EVM_ADDRESSES = {
    "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
    "0x1111111111111111111111111111111111111111",
}
APPROVED_VIDEO_SHA256 = {
    "6c479d7bfcc4143742e144a1984c2e2d718d224b26f0d1b218c9bd79aabdd1a4"
}


class ProfileContractTests(unittest.TestCase):
    def yaml(self, name):
        return yaml.safe_load((PROFILE / name).read_text(encoding="utf-8"))

    def test_required_profile_files_exist_and_parse(self):
        required = [
            "robot.profile.yaml",
            "skills.yaml",
            "functions.yaml",
            "payment-policy.yaml",
            "execution-mapping.yaml",
            "tests/skill-contract.test.yaml",
        ]
        for name in required:
            with self.subTest(name=name):
                self.assertIsInstance(self.yaml(name), dict)
        self.assertTrue((PROFILE / "docs" / "README.md").is_file())
        self.assertTrue((PROFILE / "docs" / "validation-report.md").is_file())

    def test_profile_identity_and_tier_are_consistent(self):
        profile = self.yaml("robot.profile.yaml")
        skills = self.yaml("skills.yaml")
        self.assertEqual("dobot", profile["vendor"])
        self.assertEqual("cra", profile["robotModel"])
        self.assertEqual("physical-manipulator", profile["robotType"])
        self.assertEqual("dobot.cra.cr3v-pick-place.v1", profile["profileId"])
        self.assertEqual(3, profile["submission"]["tier"])
        self.assertEqual("physical", profile["submission"]["scope"])
        self.assertIn("\u00d7", profile["displayName"])
        self.assertEqual([{"github": "Junzhe"}], profile["maintainers"])
        self.assertEqual(profile["profileId"], skills["profileId"])
        self.assertTrue(skills["skills"][0]["customBehavior"])

    def test_topics_and_payment_gate_are_consistent(self):
        profile = self.yaml("robot.profile.yaml")
        mapping = self.yaml("execution-mapping.yaml")
        policy = self.yaml("payment-policy.yaml")
        self.assertEqual(
            profile["runtime"]["actionTopic"], mapping["transport"]["actionTopic"]
        )
        self.assertEqual(
            profile["runtime"]["resultTopic"], mapping["transport"]["resultTopic"]
        )
        self.assertEqual("robopay-action.v1", mapping["transport"]["actionSchema"])
        self.assertEqual("robot-action-result.v1", mapping["transport"]["resultSchema"])
        self.assertEqual(profile["profileId"], mapping["profileId"])
        self.assertEqual(profile["profileId"], policy["profileId"])
        settlement = policy["policies"][0]["settlement"]
        self.assertEqual("success", settlement["eligibleOnlyAfterResultStatus"])
        self.assertIn("error", settlement["noSettleResultStatuses"])

    def test_example_has_all_preserved_envelope_fields(self):
        example = json.loads(
            (PROFILE / "examples" / "action-envelope.pick-place.json").read_text(
                encoding="utf-8"
            )
        )
        for field in (
            "actionId",
            "robotId",
            "skillId",
            "params",
            "paramsHash",
            "idempotencyKey",
            "payment",
        ):
            self.assertIn(field, example)
        self.assertNotIn("issuedAt", example)
        self.assertNotIn("expiresAt", example)
        for field in (
            "provider",
            "authorizationId",
            "verified",
            "status",
            "settled",
            "network",
            "asset",
            "amount",
            "payTo",
            "issuedAt",
            "expiresAt",
        ):
            self.assertIn(field, example["payment"])
        canonical = json.dumps(
            example["params"], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), example["paramsHash"])

    def test_public_profile_omits_known_private_values(self):
        forbidden = [
            re.compile(r"192\.168\."),
            re.compile(r"C:\\Users\\", re.IGNORECASE),
            re.compile(r"0x[a-fA-F0-9]{64}"),
            re.compile(r"262\.4888"),
            re.compile(r"CR3V\d{2}-\d{4}-\d{4}", re.IGNORECASE),
            re.compile(r"CC262V-\w+-\d{4}-\d{4}", re.IGNORECASE),
            re.compile(r"BEGIN (?:RSA |EC )?PRIVATE KEY"),
        ]
        for path in PROFILE.rglob("*"):
            if not path.is_file():
                continue
            if (
                path.suffix not in {".yaml", ".json", ".md", ".py", ".txt", ".lua"}
                and path.name != "LICENSE"
            ):
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in forbidden:
                with self.subTest(
                    path=path.relative_to(PROFILE), pattern=pattern.pattern
                ):
                    self.assertIsNone(pattern.search(text))
            for address in re.findall(r"0x[a-fA-F0-9]{40}", text):
                with self.subTest(path=path.relative_to(PROFILE), address=address):
                    self.assertIn(address.lower(), PUBLIC_EVM_ADDRESSES)

    def test_no_unapproved_binary_evidence_is_packaged(self):
        binary_suffixes = {".mp4", ".mov", ".png", ".jpg", ".jpeg", ".zip"}
        packaged = []
        for path in PROFILE.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in binary_suffixes:
                continue
            packaged.append(path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.subTest(path=path.relative_to(PROFILE)):
                self.assertIn(digest, APPROVED_VIDEO_SHA256)
                self.assertEqual(".mp4", path.suffix.lower())
        self.assertEqual(1, len(packaged))

    def test_controller_source_is_custom_composed_behavior(self):
        source = (PROFILE / "controller-project" / "src0.lua").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(source.count("MovJ("), 10)
        self.assertEqual(2, source.count("RelMovLUser("))
        self.assertEqual(4, source.count("DO("))
        self.assertIn("SpeedFactor(20)", source)
        commands = [
            line.strip()
            for line in source.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        ]
        self.assertEqual(
            [
                "SpeedFactor(20)",
                "VelJ(20)",
                "AccJ(20)",
                "MovJ(P1)",
                "MovJ(P2)",
                "DO(9,1)",
                "Wait(500)",
                "RelMovLUser({0,0,20,0,0,0})",
                "MovJ(P3)",
                "MovJ(P4)",
                "DO(9,0)",
                "Wait(1500)",
                "MovJ(P1)",
                "MovJ(P1)",
                "MovJ(P5)",
                "DO(9,1)",
                "Wait(500)",
                "RelMovLUser({0,0,20,0,0,0})",
                "MovJ(P3)",
                "MovJ(P6)",
                "DO(9,0)",
                "Wait(1500)",
                "MovJ(P1)",
            ],
            commands,
        )

    def test_public_source_hashes_match_manifest(self):
        manifest = self.yaml("controller-project/artifact-manifest.yaml")
        for entry_name in ("logic", "pointsTemplate"):
            entry = manifest["publicSources"][entry_name]
            normalized = (
                (PROFILE / "controller-project" / entry["file"])
                .read_text(encoding="utf-8")
                .replace("\r\n", "\n")
                .encode("utf-8")
            )
            self.assertEqual(hashlib.sha256(normalized).hexdigest(), entry["sha256"])

    def test_official_sdk_mit_license_is_preserved(self):
        license_text = (PROFILE / "vendor" / "TCP-IP-Python-V4" / "LICENSE").read_text(
            encoding="utf-8"
        )
        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) 2023 Dobot", license_text)


if __name__ == "__main__":
    unittest.main()
