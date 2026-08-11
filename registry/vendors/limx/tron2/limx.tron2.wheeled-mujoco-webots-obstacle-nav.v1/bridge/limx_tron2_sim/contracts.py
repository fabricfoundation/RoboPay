"""Fail-closed contract for the priced LimX TRON 2 navigation profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROFILE_ID = "limx.tron2.wheeled-mujoco-webots-obstacle-nav.v1"
ROBOT_ID = "limx-tron2-wf-sim-01"
NAVIGATION_SKILL = "navigate_obstacle_course"
STOP_SKILL = "stop"
ALLOWED_SKILLS = frozenset({NAVIGATION_SKILL, STOP_SKILL})
MAX_DURATION_SECONDS = 70.0


@dataclass(frozen=True)
class NavigationRequest:
    skill_id: str
    max_duration_sec: float = MAX_DURATION_SECONDS


class ContractError(ValueError):
    """Raised before a Tunnel-verified action can reach either simulator."""


def validate_action(robot_id: Any, action: Any, skill_id: Any, params: Any) -> NavigationRequest:
    """Allow only a registered, fixed and bounded profile action tuple."""

    if robot_id != ROBOT_ID:
        raise ContractError("unknown robot")
    if not isinstance(action, str) or not isinstance(skill_id, str) or action != skill_id:
        raise ContractError("action must exactly match the registered skill")
    if action not in ALLOWED_SKILLS:
        raise ContractError("unregistered skill")
    if not isinstance(params, dict):
        raise ContractError("params must be an object")
    if params:
        raise ContractError("this fixed course does not accept caller-controlled motion parameters")
    return NavigationRequest(skill_id=action)
