"""Validation tests for Hugging Face Reachy Mini bridge.

These tests validate the mapper and skill logic without requiring ROS2.
They can run standalone with just Python 3.10+.
"""
import math
import sys
import unittest
from unittest.mock import MagicMock

# We need to mock zenoh_bridge since it requires ROS2
# Create mock module before importing
mock_zenoh = MagicMock()
mock_zenoh.ActionEvent = type("ActionEvent", (), {
    "__init__": lambda self, action="", params=None, timestamp="": (
        setattr(self, "action", action),
        setattr(self, "params", params or {}),
        setattr(self, "timestamp", timestamp),
    )
})
mock_zenoh.CommandMapper = type("CommandMapper", (), {})
mock_zenoh.clamp = lambda v, lo, hi: max(lo, min(hi, v))
mock_zenoh.parse_action_event = MagicMock()
mock_zenoh.ZenohSubscriberHelper = MagicMock()

sys.modules["zenoh_bridge"] = mock_zenoh
sys.modules["geometry_msgs"] = MagicMock()
sys.modules["geometry_msgs.msg"] = MagicMock()
sys.modules["rclpy"] = MagicMock()
sys.modules["rclpy.node"] = MagicMock()
sys.modules["rclpy.executors"] = MagicMock()

# Mock geometry_msgs.msg.Twist
class MockTwist:
    def __init__(self):
        self.linear = MagicMock(x=0.0, y=0.0, z=0.0)
        self.angular = MagicMock(x=0.0, y=0.0, z=0.0)

sys.modules["geometry_msgs.msg"].Twist = MockTwist

# Now import our modules
sys.path.insert(0, ".")
from reachy_mini.mapper import ReachyMiniMapper
from reachy_mini.skills.base import RobotSkill, SkillResult
from reachy_mini.skills.obstacle_nav import ObstacleNavSkill
from reachy_mini.skills.manager import SkillManager


class TestReachyMiniMapper(unittest.TestCase):
    """Test the Reachy Mini action → Twist mapper."""

    def setUp(self):
        self.mapper = ReachyMiniMapper(
            forward_speed=0.3,
            backward_speed=0.0,
            turn_linear_speed=0.1,
            turn_angular_speed=0.2,
        )

    def _make_event(self, action, params=None):
        event = MagicMock()
        event.action = action
        event.params = params or {}
        return event

    def test_forward(self):
        twist = self.mapper.map(self._make_event("move_forward"))
        self.assertAlmostEqual(twist.linear.x, 0.3)
        self.assertAlmostEqual(twist.angular.z, 0.0)

    def test_forward_alias(self):
        twist = self.mapper.map(self._make_event("forward"))
        self.assertAlmostEqual(twist.linear.x, 0.3)

    def test_backward_no_reverse(self):
        """Reachy Mini has no reverse base travel."""
        twist = self.mapper.map(self._make_event("move_backward"))
        self.assertAlmostEqual(twist.linear.x, 0.0)

    def test_turn_left(self):
        twist = self.mapper.map(self._make_event("turn_left"))
        self.assertAlmostEqual(twist.linear.x, 0.1)
        self.assertGreater(twist.angular.z, 0.0)

    def test_turn_right(self):
        twist = self.mapper.map(self._make_event("turn_right"))
        self.assertAlmostEqual(twist.linear.x, 0.1)
        self.assertLess(twist.angular.z, 0.0)

    def test_stop(self):
        twist = self.mapper.map(self._make_event("stop"))
        self.assertAlmostEqual(twist.linear.x, 0.0)
        self.assertAlmostEqual(twist.angular.z, 0.0)

    def test_unknown_action_safe_default(self):
        twist = self.mapper.map(self._make_event("dance"))
        self.assertAlmostEqual(twist.linear.x, 0.0)
        self.assertAlmostEqual(twist.angular.z, 0.0)

    def test_velocity_bounds(self):
        """All outputs must respect Reachy Mini velocity limits."""
        actions = ["move_forward", "backward", "turn_left", "turn_right", "stop"]
        for action in actions:
            twist = self.mapper.map(self._make_event(action))
            self.assertGreaterEqual(twist.linear.x, 0.0, f"{action}: vx < 0")
            self.assertLessEqual(twist.linear.x, 0.5, f"{action}: vx > 0.5")
            self.assertGreaterEqual(twist.angular.z, -0.3, f"{action}: wz < -0.3")
            self.assertLessEqual(twist.angular.z, 0.3, f"{action}: wz > 0.3")


class TestObstacleNavSkill(unittest.TestCase):
    """Test the obstacle navigation skill."""

    def test_name(self):
        skill = ObstacleNavSkill()
        self.assertEqual(skill.name, "obstacle_navigation")

    def test_reset(self):
        skill = ObstacleNavSkill()
        skill.reset({"goal": (5.0, 5.0), "start": (0.0, 0.0), "obstacles": []})
        self.assertFalse(skill.is_complete())

    def test_goal_reached(self):
        """Skill should report success when close to goal."""
        skill = ObstacleNavSkill(goal_threshold=0.5)
        skill.reset({"goal": (1.0, 1.0), "start": (0.0, 0.0), "obstacles": []})
        result = skill.step({"position": (0.9, 0.9), "heading": 0.0})
        self.assertEqual(result.status, "success")
        self.assertTrue(skill.is_complete())

    def test_navigation_produces_velocity(self):
        """Navigating should produce non-zero velocity toward goal."""
        skill = ObstacleNavSkill(max_speed=0.5, max_angular=0.3)
        skill.reset({"goal": (5.0, 0.0), "start": (0.0, 0.0), "obstacles": []})
        result = skill.step({"position": (0.0, 0.0), "heading": 0.0})
        self.assertEqual(result.status, "running")
        self.assertGreater(result.speed, 0.0)
        self.assertLessEqual(result.speed, 0.5)
        self.assertGreaterEqual(result.angular_speed, -0.3)
        self.assertLessEqual(result.angular_speed, 0.3)

    def test_obstacle_avoidance(self):
        """Obstacles should alter the navigation path."""
        skill = ObstacleNavSkill(max_speed=0.5, max_angular=0.3)
        skill.reset({
            "goal": (5.0, 0.0),
            "start": (0.0, 0.0),
            "obstacles": [(2.5, 0.0)],
        })
        # Step a few times — angular velocity should be non-zero to avoid obstacle
        has_turn = False
        for i in range(10):
            pos_x = i * 0.3
            result = skill.step({"position": (pos_x, 0.0), "heading": 0.0})
            if abs(result.angular_speed) > 0.01:
                has_turn = True
                break
        self.assertTrue(has_turn, "Skill should produce angular velocity to avoid obstacle")

    def test_no_reverse_velocity(self):
        """Reachy Mini skill should never produce negative forward speed."""
        skill = ObstacleNavSkill(max_speed=0.5)
        skill.reset({"goal": (-5.0, 0.0), "start": (0.0, 0.0), "obstacles": []})
        for i in range(20):
            result = skill.step({"position": (0.0, 0.0), "heading": 0.0})
            self.assertGreaterEqual(result.speed, 0.0, f"Step {i}: negative speed")

    def test_timeout(self):
        """Skill should timeout after max steps."""
        skill = ObstacleNavSkill(goal_threshold=0.01)
        skill.reset({"goal": (100.0, 100.0), "start": (0.0, 0.0), "obstacles": []})
        for _ in range(1001):
            if skill.is_complete():
                break
            skill.step({"position": (0.0, 0.0), "heading": 0.0})
        self.assertTrue(skill.is_complete())

    def test_metrics(self):
        """Skill should produce meaningful metrics."""
        skill = ObstacleNavSkill(goal_threshold=0.5)
        skill.reset({"goal": (5.0, 5.0), "start": (0.0, 0.0), "obstacles": [(2.0, 2.0)]})
        result = skill.step({"position": (1.0, 1.0), "heading": 0.5})
        metrics = result.metrics
        self.assertIn("position", metrics)
        self.assertIn("target", metrics)
        self.assertIn("distance_to_goal", metrics)
        self.assertIn("path_progress", metrics)
        self.assertIn("collision_count", metrics)
        self.assertIn("obstacle_clearance", metrics)
        self.assertIn("status", metrics)

    def test_collision_tracking(self):
        """Collisions should be tracked."""
        skill = ObstacleNavSkill()
        skill.reset({"goal": (5.0, 0.0), "start": (0.0, 0.0), "obstacles": []})
        skill.step({"position": (0.0, 0.0), "heading": 0.0, "collision": True})
        skill.step({"position": (0.0, 0.0), "heading": 0.0, "collision": True})
        metrics = skill.get_metrics()
        self.assertEqual(metrics["collision_count"], 2)


class TestSkillManager(unittest.TestCase):
    """Test the skill manager routing."""

    def test_start_navigation(self):
        manager = SkillManager()
        event = MagicMock()
        event.action = "navigate_to"
        event.params = {"goal": [5.0, 5.0], "obstacles": [[2.0, 2.0]]}
        result = manager.execute(event)
        self.assertEqual(result["status"], "started")
        self.assertTrue(manager.is_active)

    def test_navigate_to_requires_goal(self):
        manager = SkillManager()
        event = MagicMock()
        event.action = "navigate_to"
        event.params = {}
        result = manager.execute(event)
        self.assertIn("error", result)

    def test_step_skill(self):
        manager = SkillManager()
        # Start navigation
        event = MagicMock()
        event.action = "navigate_to"
        event.params = {"goal": [5.0, 0.0]}
        manager.execute(event)

        # Step it
        step_event = MagicMock()
        step_event.action = "skill_step"
        step_event.params = {"state": {"position": [0.0, 0.0], "heading": 0.0}}
        result = manager.execute(step_event)
        self.assertIn("action", result)
        self.assertIn("speed", result)

    def test_cancel_skill(self):
        manager = SkillManager()
        event = MagicMock()
        event.action = "navigate_to"
        event.params = {"goal": [5.0, 5.0]}
        manager.execute(event)

        cancel_event = MagicMock()
        cancel_event.action = "skill_cancel"
        cancel_event.params = {}
        result = manager.execute(cancel_event)
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(manager.is_active)

    def test_unknown_action(self):
        manager = SkillManager()
        event = MagicMock()
        event.action = "fly"
        event.params = {}
        result = manager.execute(event)
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
