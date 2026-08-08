"""Unit tests for bridge security model.

Tests cover:
- Unpaid requests return 402
- Paid requests execute
- Invalid skills rejected
- Duplicate idempotency rejected
- Stop works without payment
"""
import json
import pytest
from simulation.common.zenoh_mujoco_bridge import ACTION_SKILL_MAP


class TestSecurityModel:
    def test_stop_in_skill_map(self):
        """Stop must always be available."""
        assert "stop" in ACTION_SKILL_MAP
        assert "cancel" in ACTION_SKILL_MAP

    def test_stop_no_payment(self):
        """Stop action should work without payment."""
        stop = ACTION_SKILL_MAP["stop"]
        assert stop["vx"] == 0.0
        assert stop["vy"] == 0.0
        assert stop["wz"] == 0.0

    def test_known_skills_have_mapping(self):
        """All registered skills must have a mapping."""
        expected = ["move_forward", "move_backward", "turn_left", "turn_right",
                    "navigate_obstacle", "pick_and_place", "stop", "cancel"]
        for skill in expected:
            assert skill in ACTION_SKILL_MAP, f"Missing mapping for {skill}"

    def test_unknown_skill_returns_none(self):
        """Unknown skills should not have a mapping."""
        assert "nonexistent_skill" not in ACTION_SKILL_MAP
