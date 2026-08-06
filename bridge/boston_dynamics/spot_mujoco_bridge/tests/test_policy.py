import os
import unittest
from unittest.mock import patch

import numpy as np

from spot_mujoco_bridge.bridge import BridgeSettings, _visual_payment_demo
from spot_mujoco_bridge.environment import SpotObstacleCourseEnvironment
from spot_mujoco_bridge.policy import SpotObstaclePolicy
from spot_mujoco_bridge.course import COURSE_GOAL, COURSE_REFERENCE_ROUTE


class SpotObstaclePolicyTests(unittest.TestCase):
    def test_bridge_deployment_settings_are_configurable(self):
        with patch.dict(
            os.environ,
            {
                "ROBOT_ID": "spot-lab-7",
                "ZENOH_ENDPOINT": "tcp/10.0.0.7:7447",
                "ZENOH_CONFIG": "/etc/robopay/zenoh.json5",
                "ZENOH_ACTION_TOPIC": "robots/spot/actions",
                "ZENOH_RESULT_TOPIC": "robots/spot/results",
                "ZENOH_METRICS_TOPIC": "robots/spot/metrics",
            },
            clear=True,
        ):
            settings = BridgeSettings.from_env()

        self.assertEqual(settings.robot_id, "spot-lab-7")
        self.assertEqual(settings.zenoh_endpoint, "tcp/10.0.0.7:7447")
        self.assertEqual(settings.zenoh_config_path, "/etc/robopay/zenoh.json5")
        self.assertEqual(settings.action_topic, "robots/spot/actions")
        self.assertEqual(settings.result_topic, "robots/spot/results")
        self.assertEqual(settings.metrics_topic, "robots/spot/metrics")

    def test_visual_paid_demo_is_opt_in_and_bounded(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_visual_payment_demo(), (False, None))
        with patch.dict(
            os.environ,
            {"SPOT_MUJOCO_VIEWER": "true", "SPOT_MUJOCO_VIEWER_HOLD_SECONDS": "5"},
            clear=True,
        ):
            self.assertEqual(_visual_payment_demo(), (True, 5.0))

    def test_route_uses_a_clearance_corridor(self):
        policy = SpotObstaclePolicy(goal=(3.0, 0.0), side="left")
        policy.reset((0.0, 0.0), [{"x": 1.4, "y": 0.0, "half_x": 0.26, "half_y": 0.32}])
        self.assertEqual(policy.waypoint_count, 3)
        self.assertGreater(policy._waypoints[0].y, 0.70)

    def test_state_feedback_changes_leg_commands(self):
        policy = SpotObstaclePolicy(goal=(3.0, 0.0))
        policy.reset((0.0, 0.0), [{"x": 1.4, "y": 0.0, "half_x": 0.26, "half_y": 0.32}])
        control, diagnostics = policy.compute_control({"position": (0.0, 0.0, 0.46), "yaw": 0.0, "sim_time": 2.25})
        self.assertEqual(control.shape, (12,))
        self.assertNotEqual(diagnostics["steering"], 0.0)
        self.assertNotEqual(control[1], control[4])

    def test_speed_scale_is_bounded_and_changes_gait_frequency(self):
        slow = SpotObstaclePolicy(goal=(3.0, 0.0), speed_scale=0.25)
        slow.reset((0.0, 0.0), [{"x": 1.4, "y": 0.0, "half_x": 0.26, "half_y": 0.32}])
        _, diagnostics = slow.compute_control(
            {"position": (0.0, 0.0, 0.46), "yaw": 0.0, "sim_time": 2.25}
        )
        self.assertEqual(diagnostics["parameters"]["gait_frequency_hz"], 0.25)
        with self.assertRaisesRegex(ValueError, "speed_scale"):
            SpotObstaclePolicy(goal=(3.0, 0.0), speed_scale=1.01)

    def test_environment_policy_overrides_cannot_exceed_safety_limits(self):
        with patch.dict(os.environ, {"SPOT_POLICY_STEER_LIMIT": "0.31"}, clear=True):
            with self.assertRaisesRegex(ValueError, "SPOT_POLICY_STEER_LIMIT"):
                SpotObstaclePolicy(goal=(3.0, 0.0))

    def test_safe_stop_applies_neutral_control_and_zero_velocity(self):
        try:
            environment = SpotObstacleCourseEnvironment()
        except FileNotFoundError as error:
            self.skipTest(f"Spot MJCF is an optional pinned download: {error}")
        environment.reset()
        policy = SpotObstaclePolicy(goal=COURSE_GOAL)
        neutral_control = policy.safe_stop_control()
        environment.data.qvel[:] = 1.0

        environment.safe_stop(neutral_control)

        np.testing.assert_allclose(environment.data.ctrl, neutral_control)
        np.testing.assert_allclose(environment.data.qvel, 0.0)
        self.assertLess(environment.min_clearance, float("inf"))

    def test_paired_course_uses_the_published_reference_route(self):
        policy = SpotObstaclePolicy(
            goal=COURSE_GOAL,
            side="left",
            reference_route=COURSE_REFERENCE_ROUTE,
        )
        policy.reset((0.0, 0.0), [{"x": 1.4, "y": 0.0, "half_x": 0.26, "half_y": 0.32}])
        self.assertEqual(tuple((waypoint.x, waypoint.y) for waypoint in policy.route), COURSE_REFERENCE_ROUTE)


if __name__ == "__main__":
    unittest.main()
