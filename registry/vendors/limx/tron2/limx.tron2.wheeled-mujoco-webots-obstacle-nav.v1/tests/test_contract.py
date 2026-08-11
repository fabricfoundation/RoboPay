from __future__ import annotations

import pytest

from limx_tron2_sim.contracts import ContractError, NAVIGATION_SKILL, ROBOT_ID, STOP_SKILL, validate_action


def test_only_registered_skill_and_exact_robot_are_accepted() -> None:
    assert validate_action(ROBOT_ID, NAVIGATION_SKILL, NAVIGATION_SKILL, {}).skill_id == NAVIGATION_SKILL
    assert validate_action(ROBOT_ID, STOP_SKILL, STOP_SKILL, {}).skill_id == STOP_SKILL
    for action, skill, params in [
        ("", "", {}),
        ("unknown", "unknown", {}),
        (NAVIGATION_SKILL, "unknown", {}),
        (NAVIGATION_SKILL, NAVIGATION_SKILL, {"duration": 100}),
        (NAVIGATION_SKILL, NAVIGATION_SKILL, []),
    ]:
        with pytest.raises(ContractError):
            validate_action(ROBOT_ID, action, skill, params)
    with pytest.raises(ContractError):
        validate_action("some-other-robot", NAVIGATION_SKILL, NAVIGATION_SKILL, {})
