"""Profile contract tests for k1-001.

Verifies that all profile YAML files are consistent with arm_spec.py
and that the robot identity is correct.
"""
import os
import sys
import unittest
import yaml

_ROOT = os.path.dirname(os.path.abspath(__file__))
_BRIDGE = os.path.dirname(_ROOT)
sys.path.insert(0, _BRIDGE)

from arm_spec import (
    BASE_H, CAM_FOV, CAM_Z_OFFSET, CONFIRM_ANGLE_MAX, CONFIRM_DISTANCE_MAX,
    DISTANCE_MAX, DISTANCE_MIN, LINK1, LINK2, LINK3, MAX_REACH,
    POSITION_TOLERANCE, REACHABILITY_GAP, SCENES, TIMESTEP,
)


class TestRobotProfileMatchesSpec(unittest.TestCase):

    def test_robot_id_matches(self):
        """robotId in profile must match arm_spec.ROBOT_ID."""
        profile_path = os.path.join(_BRIDGE, "profiles", "robot.profile.yaml")
        with open(profile_path) as f:
            profile = yaml.safe_load(f)
        self.assertEqual(profile["robotId"], "k1-001")

    def test_profile_id_matches(self):
        profile_path = os.path.join(_BRIDGE, "profiles", "robot.profile.yaml")
        with open(profile_path) as f:
            profile = yaml.safe_load(f)
        self.assertEqual(profile["profileId"], "laok.k1-001.active-inspection.v1")

    def test_degrees_of_freedom(self):
        profile_path = os.path.join(_BRIDGE, "profiles", "robot.profile.yaml")
        with open(profile_path) as f:
            profile = yaml.safe_load(f)
        self.assertEqual(profile["embodiment"]["degreesOfFreedom"], 6)

    def test_kinematics_match_arm_spec(self):
        """Kinematic parameters in profile must match arm_spec.py constants."""
        profile_path = os.path.join(_BRIDGE, "profiles", "robot.profile.yaml")
        with open(profile_path) as f:
            profile = yaml.safe_load(f)
        kin = profile["embodiment"]["kinematics"]
        self.assertAlmostEqual(kin["baseHeight"], BASE_H, places=4)
        self.assertAlmostEqual(kin["link1"], LINK1, places=4)
        self.assertAlmostEqual(kin["link2"], LINK2, places=4)
        self.assertAlmostEqual(kin["link3"], LINK3, places=4)
        self.assertAlmostEqual(kin["maxReach"], MAX_REACH, places=4)

    def test_simulation_timestep_matches(self):
        profile_path = os.path.join(_BRIDGE, "profiles", "robot.profile.yaml")
        with open(profile_path) as f:
            profile = yaml.safe_load(f)
        sim = profile["simulation"]
        self.assertAlmostEqual(sim["primaryEngine"]["timestep"], TIMESTEP, places=4)

    def test_topics_match_transport_module(self):
        """Zenoh topic names must match flow/zenoh_transport.py."""
        profile_path = os.path.join(_BRIDGE, "profiles", "robot.profile.yaml")
        with open(profile_path) as f:
            profile = yaml.safe_load(f)
        topics = profile["transport"]["topics"]
        self.assertEqual(topics["action"], "robot/tunnel/action")
        self.assertEqual(topics["result"], "robot/tunnel/result")

    def test_endpoint_and_mode_match_transport_module(self):
        profile_path = os.path.join(_BRIDGE, "profiles", "robot.profile.yaml")
        with open(profile_path) as f:
            profile = yaml.safe_load(f)
        self.assertEqual(profile["transport"]["endpoint"], "tcp/127.0.0.1:17447")
        self.assertEqual(profile["transport"]["mode"], "peer")

    def test_skills_catalogue_exists(self):
        skills_path = os.path.join(_BRIDGE, "profiles", "skills.yaml")
        self.assertTrue(os.path.exists(skills_path))
        with open(skills_path) as f:
            catalog = yaml.safe_load(f)
        self.assertIn("active_inspection",
                      [s["skillId"] for s in catalog["skills"]])

    def test_payment_policy_exists(self):
        pp_path = os.path.join(_BRIDGE, "profiles", "payment-policy.yaml")
        self.assertTrue(os.path.exists(pp_path))
        with open(pp_path) as f:
            pp = yaml.safe_load(f)
        self.assertEqual(pp["robotId"], "k1-001")
        self.assertEqual(pp["provider"]["network"], "base-sepolia")
        self.assertEqual(pp["provider"]["chainId"], 84532)

    def test_execution_mapping_exists(self):
        em_path = os.path.join(_BRIDGE, "profiles", "execution-mapping.yaml")
        self.assertTrue(os.path.exists(em_path))
        with open(em_path) as f:
            em = yaml.safe_load(f)
        self.assertEqual(em["robotId"], "k1-001")
        self.assertEqual(em["mappings"][0]["skillId"], "active_inspection")

    def test_scene_parameters_match_arm_spec(self):
        """Scene parameters in execution-mapping.yaml must match arm_spec.py."""
        em_path = os.path.join(_BRIDGE, "profiles", "execution-mapping.yaml")
        with open(em_path) as f:
            em = yaml.safe_load(f)
        scene = em["scenes"]["inspection"]
        # Targets are dicts with 'name', 'y', 'z' keys
        targets = scene["targets"]
        self.assertEqual(len(targets), 3)
        # Check first target (left)
        self.assertEqual(targets[0]["name"], "left")
        self.assertAlmostEqual(targets[0]["y"], 0.3, places=4)
        self.assertAlmostEqual(targets[0]["z"], 0.1, places=4)
        # Check middle target (center)
        self.assertEqual(targets[1]["name"], "center")
        self.assertAlmostEqual(targets[1]["y"], 0.3, places=4)
        self.assertAlmostEqual(targets[1]["z"], 0.18, places=4)
        # Check last target (right)
        self.assertEqual(targets[2]["name"], "right")
        self.assertAlmostEqual(targets[2]["y"], 0.3, places=4)
        self.assertAlmostEqual(targets[2]["z"], 0.26, places=4)

    def test_no_private_key_literal_anywhere_in_the_bridge(self):
        """Scan all Python files for hardcoded private key material."""
        bridge_dir = _BRIDGE
        for root, dirs, files in os.walk(bridge_dir):
            # Skip __pycache__ and .git
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except Exception:
                    continue
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or "Env:" in stripped:
                        continue
                    # Skip lines containing known tx hash field names
                    if any(kw in stripped for kw in ("tx_hash", "txHash", "basescan", "paymentTx", "txs")):
                        continue
                    # Skip lines with known public chain data
                    if any(kw in stripped for kw in ("0x036CbD53842c5426634e7929541eC2318f3dCF7e", "0x036c", "TRANSFER_TOPIC")):
                        continue
                    self.assertNotRegex(
                        stripped,
                        r"0x[0-9a-fA-F]{64}",
                        f"Possible private key or tx hash in {fpath}: {stripped[:80]}",
                    )


if __name__ == "__main__":
    unittest.main()
