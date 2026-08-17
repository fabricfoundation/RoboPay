"""Fail-closed contract for the bounded X30 Pro inspection-gait skill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROFILE_ID = "deep-robotics.x30-pro.mujoco-webots-inspection.v1"
ROBOT_ID = "x30-pro-sim-01"
INSPECTION_SKILL = "perform_inspection_gait"
# Compatibility aliases are Python-only; they do not widen the public catalog.
DRIVE_SKILL = INSPECTION_SKILL
STOP_SKILL = "stop"
ALLOWED_SKILLS = frozenset({INSPECTION_SKILL, STOP_SKILL})
GAIT_CYCLES = 34
MAX_DURATION_SECONDS = 45.0


@dataclass(frozen=True)
class InspectionRequest:
    skill_id: str
    gait_cycles: int = GAIT_CYCLES
    hip_sweep_rad: float = 0.10
    max_duration_sec: float = MAX_DURATION_SECONDS


DriveRequest = InspectionRequest


class ContractError(ValueError):
    """Raised before simulator publication for an unsupported action."""


def validate_action(robot_id: Any, action: Any, skill_id: Any, params: Any) -> InspectionRequest:
    """Accept only this profile's exact robot/action/skill tuple."""

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
        return InspectionRequest(skill_id=STOP_SKILL)
    if params:
        raise ContractError("perform_inspection_gait uses the profile's fixed bounded route and accepts no parameters")
    return InspectionRequest(skill_id=INSPECTION_SKILL)
