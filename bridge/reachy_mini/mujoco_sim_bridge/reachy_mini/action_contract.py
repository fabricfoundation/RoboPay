"""Robot-scoped, fail-closed action contract for the Reachy Mini bridge."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


PROFILE_ID = "pollen-robotics.reachy-mini.mujoco-webots-sim.v1"
TRACKABLE_OBJECTS = frozenset({"apple", "croissant", "duck"})
REGISTERED_SKILLS = frozenset({"look_at_apple", "inspect_table", "stop"})


@dataclass(frozen=True)
class ActionContractError(ValueError):
    """Stable failure reported before simulator actuation."""

    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActionContractError("INVALID_PARAMS", f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ActionContractError(
            "INVALID_PARAMS", f"{name} must be between {minimum:g} and {maximum:g}"
        )
    return value


def _exact_keys(params: dict[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(set(params) - allowed)
    if unexpected:
        raise ActionContractError(
            "INVALID_PARAMS", f"unregistered parameter(s): {', '.join(unexpected)}"
        )


def _validate_correlation(event: Any, expected_robot_id: str) -> None:
    fields = ("action_id", "robot_id", "skill_id", "params_hash", "idempotency_key")
    if any(not isinstance(getattr(event, field, None), str) or not getattr(event, field).strip() for field in fields):
        raise ActionContractError("MISSING_CORRELATION", "complete action correlation metadata is required")
    if event.robot_id != expected_robot_id:
        raise ActionContractError("WRONG_ROBOT", "action targets a different robot")
    if not isinstance(event.action, str) or event.action != event.skill_id:
        raise ActionContractError("ACTION_SKILL_MISMATCH", "action and skill_id must match exactly")


def validate_action_event(event: Any, expected_robot_id: str) -> str:
    """Validate a Tunnel-verified event before any Reachy control is touched.

    Returns the canonical registered skill. It deliberately accepts no mapper
    fallbacks: aliases and unknown strings cannot become object tracking.
    """

    _validate_correlation(event, expected_robot_id)
    if not isinstance(event.params, dict):
        raise ActionContractError("INVALID_PARAMS", "params must be an object")
    skill_id = event.skill_id
    if skill_id not in REGISTERED_SKILLS:
        raise ActionContractError("UNREGISTERED_ACTION", "action is not a registered Reachy skill")

    params = event.params
    if skill_id == "stop":
        if params:
            raise ActionContractError("INVALID_PARAMS", "stop does not accept parameters")
        return skill_id

    if skill_id == "look_at_apple":
        _exact_keys(params, {"target_object", "duration"})
        target = params.get("target_object")
        if target not in TRACKABLE_OBJECTS:
            raise ActionContractError("INVALID_PARAMS", "target_object must be apple, croissant, or duck")
        if "duration" in params:
            _number(params["duration"], "duration", 0.1, 30.0)
        return skill_id

    _exact_keys(params, {"targets", "per_target_duration"})
    targets = params.get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 3:
        raise ActionContractError("INVALID_PARAMS", "targets must contain one to three objects")
    if any(target not in TRACKABLE_OBJECTS for target in targets):
        raise ActionContractError("INVALID_PARAMS", "targets must contain only apple, croissant, or duck")
    if len(set(targets)) != len(targets):
        raise ActionContractError("INVALID_PARAMS", "targets must not contain duplicates")
    if "per_target_duration" in params:
        _number(params["per_target_duration"], "per_target_duration", 2.0, 8.0)
    return skill_id
