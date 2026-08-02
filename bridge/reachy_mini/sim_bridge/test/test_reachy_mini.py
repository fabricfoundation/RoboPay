"""Standalone tests -- no ROS2, no MuJoCo/Webots runtime required.

Run with: python -m pytest test/test_reachy_mini.py -v
or:       python test/test_reachy_mini.py
"""
import math
import os
import sys
import unittest
from dataclasses import dataclass

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "src"))
sys.path.insert(0, os.path.join(_here, ".."))

from policy.controller import GazePolicyConfig, ReachyGazePolicy, GazeState  # noqa: E402


@dataclass
class FakeActionEvent:
    action: str
    params: dict


class TestMapper(unittest.TestCase):
    def setUp(self):
        # local import to avoid dragging in zenoh_bridge dependency for tests
        sys.path.insert(0, os.path.join(_here, "..", "reachy_mini_bridge"))
        import types
        fake_zenoh_bridge = types.ModuleType("zenoh_bridge")
        fake_zenoh_bridge.ActionEvent = FakeActionEvent
        sys.modules["zenoh_bridge"] = fake_zenoh_bridge
        from mapper import ReachyMiniMapper
        self.mapper = ReachyMiniMapper()

    def test_look_at_named_target(self):
        cmd = self.mapper.map(FakeActionEvent("look_at", {"target": "apple"}))
        self.assertEqual(cmd.mode, "track")
        self.assertEqual(cmd.target_name, "apple")

    def test_look_at_xy_target(self):
        cmd = self.mapper.map(FakeActionEvent("look_at", {"x": 0.2, "y": -0.1}))
        self.assertEqual(cmd.mode, "track")
        self.assertEqual(cmd.target_xy, (0.2, -0.1))

    def test_look_at_missing_target_holds(self):
        cmd = self.mapper.map(FakeActionEvent("look_at", {}))
        self.assertEqual(cmd.mode, "hold")

    def test_reset_gaze(self):
        cmd = self.mapper.map(FakeActionEvent("reset_gaze", {}))
        self.assertEqual(cmd.mode, "reset")

    def test_unknown_action_holds(self):
        cmd = self.mapper.map(FakeActionEvent("do_a_backflip", {}))
        self.assertEqual(cmd.mode, "hold")


class TestGazePolicy(unittest.TestCase):
    """Tests against the current ReachyGazePolicy API: step() takes
    (target_visible, angular_error_rad) and returns state/angular_error_rad/
    locked/command_issued only. All head geometry/IK lives in the env
    wrappers (mujoco_env.py / webots_env.py), not in the policy -- these
    tests deliberately do not touch yaw/pitch, since the policy no longer
    produces them.
    """
    def setUp(self):
        self.policy = ReachyGazePolicy(GazePolicyConfig())

    def test_search_state_when_no_target(self):
        out = self.policy.step(target_visible=False, angular_error_rad=None)
        self.assertEqual(out.state, "SEARCH")
        self.assertFalse(out.locked)
        self.assertFalse(out.command_issued)

    def test_search_state_when_error_missing_even_if_visible_flag_true(self):
        # angular_error_rad=None must force SEARCH regardless of target_visible,
        # since the policy treats "no error signal" as "no usable target".
        out = self.policy.step(target_visible=True, angular_error_rad=None)
        self.assertEqual(out.state, "SEARCH")
        self.assertFalse(out.locked)

    def test_acquire_state_when_error_above_tolerance(self):
        cfg = GazePolicyConfig(lock_tolerance_rad=0.30, lock_hold_steps=15)
        policy = ReachyGazePolicy(cfg)
        out = policy.step(target_visible=True, angular_error_rad=0.5)
        self.assertEqual(out.state, "ACQUIRE")
        self.assertFalse(out.locked)
        self.assertTrue(out.command_issued)

    def test_reaches_locked_state_after_hold_steps_under_tolerance(self):
        cfg = GazePolicyConfig(lock_tolerance_rad=0.30, lock_hold_steps=15)
        policy = ReachyGazePolicy(cfg)
        out = None
        for _ in range(cfg.lock_hold_steps):
            out = policy.step(target_visible=True, angular_error_rad=0.0)
        self.assertEqual(out.state, "LOCKED")
        self.assertTrue(out.locked)

    def test_lock_counter_resets_when_error_spikes(self):
        """Proves the FSM is reactive step-to-step, not a fixed timer --
        an error spike above tolerance must reset progress toward LOCKED."""
        cfg = GazePolicyConfig(lock_tolerance_rad=0.30, lock_hold_steps=15)
        policy = ReachyGazePolicy(cfg)
        for _ in range(cfg.lock_hold_steps - 1):
            policy.step(target_visible=True, angular_error_rad=0.0)
        # one bad step right before it would have locked
        spike_out = policy.step(target_visible=True, angular_error_rad=0.5)
        self.assertEqual(spike_out.state, "ACQUIRE")
        self.assertFalse(spike_out.locked)

    def test_losing_target_returns_to_search_from_locked(self):
        cfg = GazePolicyConfig(lock_tolerance_rad=0.30, lock_hold_steps=15)
        policy = ReachyGazePolicy(cfg)
        for _ in range(cfg.lock_hold_steps):
            policy.step(target_visible=True, angular_error_rad=0.0)
        out = policy.step(target_visible=False, angular_error_rad=None)
        self.assertEqual(out.state, "SEARCH")
        self.assertFalse(out.locked)

    def test_reset_clears_state(self):
        cfg = GazePolicyConfig(lock_tolerance_rad=0.30, lock_hold_steps=15)
        policy = ReachyGazePolicy(cfg)
        for _ in range(cfg.lock_hold_steps):
            policy.step(target_visible=True, angular_error_rad=0.0)
        policy.reset()
        self.assertEqual(policy.state, GazeState.SEARCH)
        self.assertEqual(policy._lock_counter, 0)


if __name__ == "__main__":
    unittest.main()
