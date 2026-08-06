from __future__ import annotations

import unittest

from m20_pro_mujoco_bridge.contracts import (
    DRIVE_SKILL,
    ROBOT_ID,
    STOP_SKILL,
    ContractError,
    validate_action,
)


class M20ContractTests(unittest.TestCase):
    def test_bounded_obstacle_navigation_is_accepted(self) -> None:
        request = validate_action(
            ROBOT_ID,
            DRIVE_SKILL,
            DRIVE_SKILL,
            {"goalDistanceM": 1.35, "wheelSpeedRadS": 4.0, "maxDurationSec": 16.0},
        )
        self.assertEqual(request.skill_id, DRIVE_SKILL)
        self.assertEqual(request.goal_distance_m, 1.35)

    def test_missing_unknown_and_mismatched_actions_fail_closed(self) -> None:
        invalid = [
            (ROBOT_ID, None, None, {}),
            (ROBOT_ID, "unknown", "unknown", {}),
            (ROBOT_ID, DRIVE_SKILL, STOP_SKILL, {}),
            ("other-robot", DRIVE_SKILL, DRIVE_SKILL, {}),
            (ROBOT_ID, DRIVE_SKILL, DRIVE_SKILL, {"wheelSpeedRadS": 99}),
            (ROBOT_ID, DRIVE_SKILL, DRIVE_SKILL, {"unexpected": True}),
        ]
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ContractError):
                    validate_action(*values)

    def test_stop_is_parameterless_and_never_becomes_drive(self) -> None:
        request = validate_action(ROBOT_ID, STOP_SKILL, STOP_SKILL, {})
        self.assertEqual(request.skill_id, STOP_SKILL)
        with self.assertRaises(ContractError):
            validate_action(ROBOT_ID, STOP_SKILL, STOP_SKILL, {"goalDistanceM": 1.35})


if __name__ == "__main__":
    unittest.main(verbosity=2)
