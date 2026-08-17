from __future__ import annotations

import threading
import unittest

from x30_pro_mujoco_bridge.contracts import DRIVE_SKILL, ROBOT_ID, validate_action
from x30_pro_mujoco_bridge.course import MIN_FORWARD_PROGRESS_M
from x30_pro_mujoco_bridge.runtime import run_drive_episode


def _request():
    return validate_action(
        ROBOT_ID,
        DRIVE_SKILL,
        DRIVE_SKILL,
        {},
    )


class X30MuJoCoRuntimeTests(unittest.TestCase):
    def test_vendor_model_physically_traverses_the_inspection_lane(self) -> None:
        result = run_drive_episode(_request())
        self.assertTrue(result["success"], result)
        self.assertTrue(result["task_goal_reached"], result)
        self.assertEqual(
            [item["phase"] for item in result["controller_phase_transitions"]],
            ["settle", "evade_first", "pass_first", "evade_second", "pass_second", "goal_hold"],
        )
        self.assertGreaterEqual(result["body_forward_progress_m"], MIN_FORWARD_PROGRESS_M)
        self.assertGreaterEqual(result["max_positive_route_side_offset_m"], 0.55)
        self.assertGreaterEqual(result["min_base_height_m"], 0.30)
        self.assertLessEqual(result["max_tilt_rad"], 0.70)
        self.assertFalse(result["course"]["obstacle_collision_observed"])
        self.assertTrue(result["finite_state"])
        self.assertGreaterEqual(result["measured_hip_sweep_rad"], 0.12)

    def test_stop_event_returns_failure_after_zero_command_safe_stop(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        result = run_drive_episode(_request(), stop_event=stop_event)
        self.assertFalse(result["success"], result)
        self.assertTrue(result["safe_stop_applied"], result)
        self.assertEqual(result["completion_reason"], "safe_stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
