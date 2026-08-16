import os
import unittest
from unittest.mock import patch

import numpy as np

from go2_mujoco_bridge.bridge import BridgeSettings, _visual_payment_demo
from go2_mujoco_bridge.course import COURSE_GOAL, COURSE_OBSTACLES, COURSE_REFERENCE_ROUTE
from go2_mujoco_bridge.environment import Go2ObstacleCourseEnvironment
from go2_mujoco_bridge.policy import Go2ObstaclePolicy, foot_inverse_kinematics


class Go2ObstaclePolicyTests(unittest.TestCase):
    def test_bridge_deployment_settings_are_configurable(self):
        with patch.dict(os.environ, {"ROBOT_ID": "go2-lab-7", "ZENOH_ENDPOINT": "tcp/10.0.0.7:7447"}, clear=True):
            settings = BridgeSettings.from_env()
        self.assertEqual(settings.robot_id, "go2-lab-7")
        self.assertEqual(settings.zenoh_endpoint, "tcp/10.0.0.7:7447")

    def test_visual_paid_demo_is_opt_in_and_bounded(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_visual_payment_demo(), (False, None))
        with patch.dict(os.environ, {"GO2_MUJOCO_VIEWER": "true", "GO2_MUJOCO_VIEWER_HOLD_SECONDS": "5"}, clear=True):
            self.assertEqual(_visual_payment_demo(), (True, 5.0))

    def test_official_leg_inverse_kinematics_matches_home_pose(self):
        thigh, calf = foot_inverse_kinematics(0.0, -0.285)
        self.assertAlmostEqual(thigh, -calf / 2.0)
        self.assertGreater(thigh, 0.6)
        self.assertLess(calf, -1.3)

    def test_feedback_and_phase_change_joint_targets(self):
        policy = Go2ObstaclePolicy(COURSE_GOAL, "left", COURSE_REFERENCE_ROUTE)
        policy.reset((0.0, 0.0), COURSE_OBSTACLES)
        straight, first = policy.desired_joints({"position": (0.0, 0.0, 0.3), "yaw": 0.0, "sim_time": 2.1})
        turned, second = policy.desired_joints({"position": (0.0, 0.0, 0.3), "yaw": -0.5, "sim_time": 2.2})
        self.assertEqual(straight.shape, (12,))
        self.assertNotEqual(first["steering"], second["steering"])
        self.assertFalse(np.allclose(straight, turned))

    def test_speed_scale_is_bounded(self):
        slow = Go2ObstaclePolicy(COURSE_GOAL, "left", COURSE_REFERENCE_ROUTE, speed_scale=0.5)
        slow.reset((0.0, 0.0), COURSE_OBSTACLES)
        _, state = slow.desired_joints({"position": (0.0, 0.0, 0.3), "yaw": 0.0, "sim_time": 2.0})
        self.assertEqual(state["parameters"]["gait_frequency_hz"], 0.625)
        with self.assertRaisesRegex(ValueError, "speed_scale"):
            Go2ObstaclePolicy(COURSE_GOAL, speed_scale=1.01)

    def test_safe_stop_zeros_velocity_and_returns_neutral_pose(self):
        try:
            environment = Go2ObstacleCourseEnvironment()
        except FileNotFoundError as error:
            self.skipTest(str(error))
        policy = Go2ObstaclePolicy(COURSE_GOAL, "left", COURSE_REFERENCE_ROUTE)
        environment.reset(policy.neutral_joint_targets)
        environment.data.qvel[:] = 1.0
        environment.safe_stop(policy.neutral_joint_targets)
        np.testing.assert_allclose(environment.data.ctrl, 0.0)
        np.testing.assert_allclose(environment.data.qvel, 0.0)
        np.testing.assert_allclose(environment.data.qpos[environment.qpos_addresses], policy.neutral_joint_targets)

    def test_paired_course_uses_published_route(self):
        policy = Go2ObstaclePolicy(COURSE_GOAL, "left", COURSE_REFERENCE_ROUTE)
        self.assertEqual(tuple((item.x, item.y) for item in policy.route), COURSE_REFERENCE_ROUTE)


if __name__ == "__main__":
    unittest.main()
