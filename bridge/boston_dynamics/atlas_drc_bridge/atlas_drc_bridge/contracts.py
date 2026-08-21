"""Fail-closed action contract for the Atlas DRC legacy profile."""

from __future__ import annotations

import math
from dataclasses import dataclass


ATLAS_ROBOT_ID = "atlas-drc-mujoco-webots-sim-01"
PROFILE_ID = "boston-dynamics.atlas-drc.mujoco-webots-wave.v1"
WAVE_SKILL_ID = "wave_right_arm"
STOP_SKILL_ID = "stop"
ALLOWED_ACTIONS = {WAVE_SKILL_ID, STOP_SKILL_ID}


class ActionContractError(ValueError):
    """A stable, safe-to-publish bridge rejection."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WaveParameters:
    cycles: int
    amplitude_rad: float
    max_duration_sec: float


def _finite_number(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActionContractError("INVALID_PARAMS", f"{name} must be a number.")
    rendered = float(value)
    if not math.isfinite(rendered) or not minimum <= rendered <= maximum:
        raise ActionContractError(
            "INVALID_PARAMS", f"{name} must be between {minimum} and {maximum}."
        )
    return rendered


def validate_wave_params(params: object) -> WaveParameters:
    """Validate the bounded state-feedback wave skill; unknown fields fail closed."""

    if not isinstance(params, dict):
        raise ActionContractError("INVALID_PARAMS", "params must be an object.")
    allowed = {"cycles", "amplitudeRad", "maxDurationSec"}
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ActionContractError("INVALID_PARAMS", f"Unknown parameter(s): {', '.join(unknown)}.")

    cycles = params.get("cycles", 2)
    if isinstance(cycles, bool) or not isinstance(cycles, int) or not 1 <= cycles <= 3:
        raise ActionContractError("INVALID_PARAMS", "cycles must be an integer from 1 to 3.")
    amplitude = _finite_number(params.get("amplitudeRad", 0.30), "amplitudeRad", 0.15, 0.40)
    # A fixed public lower bound keeps the Tunnel catalog and the bridge
    # contract identical. Five seconds accommodates the maximum three-cycle
    # request without accepting a paid request that the bridge would later
    # reject only after Zenoh publication.
    duration = _finite_number(params.get("maxDurationSec", 8.0), "maxDurationSec", 5.0, 15.0)
    return WaveParameters(cycles=cycles, amplitude_rad=amplitude, max_duration_sec=duration)


def validate_action(action: object, params: object) -> WaveParameters | None:
    """Return validated wave parameters, or ``None`` for a parameterless safe stop."""

    if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
        raise ActionContractError("UNREGISTERED_ACTION", "Action is not registered for this Atlas profile.")
    if action == STOP_SKILL_ID:
        if params not in ({}, None):
            raise ActionContractError("INVALID_PARAMS", "stop does not accept parameters.")
        return None
    return validate_wave_params(params)
