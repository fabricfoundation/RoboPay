import json
from pathlib import Path
import unittest

import yaml

from go2_mujoco_bridge.bridge import ALLOWED_ACTIONS, PROFILE_ID
from go2_mujoco_bridge.control_core import Go2ObstacleControlCore, POLICY_ID


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROOT = PACKAGE_ROOT.parents[2]
PROFILE = ROOT / "registry/vendors/unitree/go2/unitree.go2.mujoco-webots-obstacle-nav.v1"


def load_yaml(name: str) -> dict:
    return yaml.safe_load((PROFILE / name).read_text(encoding="utf-8"))


class Go2RegistryContractTests(unittest.TestCase):
    def test_profile_catalog_payment_and_runtime_do_not_drift(self):
        profile = load_yaml("robot.profile.yaml")
        skills = load_yaml("skills.yaml")
        payment = load_yaml("payment-policy.yaml")
        mapping = load_yaml("execution-mapping.yaml")
        catalog = json.loads((PROFILE / "skill-catalog.json").read_text(encoding="utf-8"))
        lock = json.loads((PACKAGE_ROOT / "models/model.lock.json").read_text(encoding="utf-8"))

        skill_ids = {item["skillId"] for item in skills["skills"]}
        self.assertEqual(profile["profileId"], PROFILE_ID)
        self.assertEqual(profile["vendor"], "unitree")
        self.assertEqual(profile["robotModel"], "go2")
        self.assertEqual(skill_ids, ALLOWED_ACTIONS)
        self.assertEqual({item["skill_id"] for item in catalog}, skill_ids)
        self.assertEqual({item["skillId"] for item in payment["policies"]}, skill_ids)
        self.assertEqual(set(mapping["mappings"]), skill_ids)
        self.assertEqual({item["price_usdc"] for item in catalog}, {"0.001"})
        self.assertEqual({item["priceUSDC"] for item in payment["policies"]}, {"0.001"})
        self.assertEqual(profile["simulation"]["model"]["source"], lock["mujoco"]["source"])
        self.assertEqual(profile["simulation"]["model"]["commit"], lock["mujoco"]["commit"])
        self.assertIn(POLICY_ID, mapping["mappings"]["navigate_obstacles"]["policy"])

        nav_skill = next(item for item in skills["skills"] if item["skillId"] == "navigate_obstacles")
        speed = nav_skill["params"]["speedScale"]
        self.assertEqual(speed["min"], 0.5)
        self.assertEqual(speed["max"], 1.0)
        self.assertEqual(nav_skill["movementLimits"]["maxGaitFrequencyHz"], Go2ObstacleControlCore.GAIT_FREQUENCY_HZ)


if __name__ == "__main__":
    unittest.main()
