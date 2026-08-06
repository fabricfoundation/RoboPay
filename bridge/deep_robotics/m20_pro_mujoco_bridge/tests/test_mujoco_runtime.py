from __future__ import annotations

import threading
import unittest

from m20_pro_mujoco_bridge.contracts import DRIVE_SKILL, ROBOT_ID, validate_action
from m20_pro_mujoco_bridge.runtime import run_drive_episode


def _request():
    return validate_action(
        ROBOT_ID,
        DRIVE_SKILL,
        DRIVE_SKILL,
        {"goalDistanceM": 1.35, "wheelSpeedRadS": 4.0, "maxDurationSec": 16.0},
    )


class M20MuJoCoRuntimeTests(unittest.TestCase):
    def test_vendor_model_yields_to_physical_obstacle_and_resumes(self) -> None:
        result = run_drive_episode(_request())
        self.assertTrue(result["success"], result)
        self.assertGreaterEqual(result["measured_forward_distance_m"], 1.35)
        self.assertGreaterEqual(result["min_base_height_m"], 0.45)
        self.assertLessEqual(result["max_tilt_rad"], 0.35)
        self.assertTrue(result["finite_state"])
        self.assertTrue(result["course"]["obstacle_detected"])
        self.assertTrue(result["course"]["obstacle_released"])
        self.assertFalse(result["course"]["collision_detected"])
        self.assertGreaterEqual(result["course"]["yield_duration_seconds"], 3.0)

    def test_stop_event_returns_failure_after_zero_command_safe_stop(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        result = run_drive_episode(_request(), stop_event=stop_event)
        self.assertFalse(result["success"], result)
        self.assertTrue(result["safe_stop_applied"], result)
        self.assertEqual(result["completion_reason"], "safe_stopped")


if __name__ == "__main__":
    unittest.main(verbosity=2)
