"""Fail-closed contract for bounded Lynx M20 Pro obstacle navigation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROFILE_ID = "deep-robotics.lynx-m20-pro.mujoco-webots-obstacle-nav.v1"
ROBOT_ID = "lynx-m20-pro-sim-01"
NAVIGATION_SKILL = "navigate_obstacle_course"
# Retained as an internal compatibility alias for existing runner imports.
DRIVE_SKILL = NAVIGATION_SKILL
STOP_SKILL = "stop"
ALLOWED_SKILLS = frozenset({DRIVE_SKILL, STOP_SKILL})


@dataclass(frozen=True)
class NavigationRequest:
    skill_id: str
    goal_distance_m: float = 1.35
    wheel_speed_rad_s: float = 4.0
    max_duration_sec: float = 16.0


# ``DriveRequest`` remains a Python-only alias while callers transition.  It
# is not an action name and cannot widen the public allowlist.
DriveRequest = NavigationRequest


class ContractError(ValueError):
    """Raised before simulator publication for any unsupported action."""


def _number(params: dict[str, Any], key: str, minimum: float, maximum: float, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{key} must be a number")
    value = float(value)
    if not minimum <= value <= maximum:
        raise ContractError(f"{key} must be in [{minimum}, {maximum}]")
    return value


def validate_action(robot_id: Any, action: Any, skill_id: Any, params: Any) -> NavigationRequest:
    """Accept only the catalog's exact robot/action/skill tuple."""

    if robot_id != ROBOT_ID:
        raise ContractError("unknown robot")
    if not isinstance(action, str) or not isinstance(skill_id, str) or action != skill_id:
        raise ContractError("action must exactly match the registered skill")
    if action not in ALLOWED_SKILLS:
        raise ContractError("unregistered skill")
    if not isinstance(params, dict):
        raise ContractError("params must be an object")
    if action == STOP_SKILL:
        if params:
            raise ContractError("stop does not accept parameters")
        return NavigationRequest(skill_id=STOP_SKILL)
    # These public field names are exactly the Tunnel skill-catalog names.
    # The dataclass keeps Pythonic internal names only after validation.
    expected = {"goalDistanceM", "wheelSpeedRadS", "maxDurationSec"}
    unknown = set(params) - expected
    if unknown:
        raise ContractError(f"unknown parameters: {sorted(unknown)}")
    return NavigationRequest(
        skill_id=DRIVE_SKILL,
        goal_distance_m=_number(params, "goalDistanceM", 1.25, 1.55, 1.35),
        wheel_speed_rad_s=_number(params, "wheelSpeedRadS", 2.0, 6.0, 4.0),
        max_duration_sec=_number(params, "maxDurationSec", 12.0, 20.0, 16.0),
    )
