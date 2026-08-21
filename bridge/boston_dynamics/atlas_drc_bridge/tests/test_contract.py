"""Fast contract tests for Atlas action routing and movement bounds."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from atlas_drc_bridge.contracts import (
    STOP_SKILL_ID,
    WAVE_SKILL_ID,
    ActionContractError,
    validate_action,
    validate_wave_params,
)
from atlas_drc_bridge.runtime import ArmWavePolicy


class AtlasContractTests(unittest.TestCase):
    def test_bounded_wave_is_accepted(self) -> None:
        params = validate_action(
            WAVE_SKILL_ID,
            {"cycles": 2, "amplitudeRad": 0.30, "maxDurationSec": 8},
        )
        self.assertIsNotNone(params)
        self.assertEqual(params.cycles, 2)

    def test_missing_unknown_and_oversized_values_fail_closed(self) -> None:
        for payload in (
            {"cycles": 2, "amplitudeRad": 0.41, "maxDurationSec": 8},
            {"cycles": 2, "amplitudeRad": 0.30, "maxDurationSec": 1},
            {"cycles": 2, "amplitudeRad": 0.30, "maxDurationSec": 8, "target": "payer"},
        ):
            with self.subTest(payload=payload), self.assertRaises(ActionContractError):
                validate_wave_params(payload)

    def test_unknown_action_has_no_fallback(self) -> None:
        with self.assertRaises(ActionContractError) as raised:
            validate_action("object_tracking", {})
        self.assertEqual(raised.exception.code, "UNREGISTERED_ACTION")

    def test_stop_is_parameterless_and_does_not_turn_into_wave(self) -> None:
        self.assertIsNone(validate_action(STOP_SKILL_ID, {}))
        with self.assertRaises(ActionContractError):
            validate_action(STOP_SKILL_ID, {"cycles": 2})

    def test_policy_advances_only_from_measured_turning_point(self) -> None:
        params = validate_wave_params({"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 5})
        policy = ArmWavePolicy(params)
        for _ in range(5):
            policy.observe(0.21, 2.0)
        self.assertEqual(policy.phase_index, 0)
        policy.observe(0.21, 2.0)
        self.assertEqual(policy.phase_index, 1)
        self.assertFalse(policy.complete)


if __name__ == "__main__":
    unittest.main(verbosity=2)
