from __future__ import annotations

import unittest

from x30_pro_mujoco_bridge.contracts import (
    DRIVE_SKILL,
    ROBOT_ID,
    STOP_SKILL,
    ContractError,
    validate_action,
)


class X30ContractTests(unittest.TestCase):
    def test_fixed_inspection_lane_is_accepted(self) -> None:
        request = validate_action(
            ROBOT_ID,
            DRIVE_SKILL,
            DRIVE_SKILL,
            {},
        )
        self.assertEqual(request.skill_id, DRIVE_SKILL)
        self.assertEqual(request.gait_cycles, 34)
        self.assertEqual(request.hip_sweep_rad, 0.10)
        self.assertEqual(request.max_duration_sec, 45.0)

    def test_missing_unknown_and_mismatched_actions_fail_closed(self) -> None:
        invalid = [
            (ROBOT_ID, None, None, {}),
            (ROBOT_ID, "unknown", "unknown", {}),
            (ROBOT_ID, DRIVE_SKILL, STOP_SKILL, {}),
            ("other-robot", DRIVE_SKILL, DRIVE_SKILL, {}),
            (ROBOT_ID, DRIVE_SKILL, DRIVE_SKILL, {"gaitCycles": 3}),
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
            validate_action(ROBOT_ID, STOP_SKILL, STOP_SKILL, {"gaitCycles": 3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
