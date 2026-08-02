"""Gaze-tracking policy for Reachy Mini.

Delegates all head geometry/IK to the vendor's own
look_at_world_pose + AnalyticalKinematics (via ReachyMiniMujocoEnv).
This policy's job is purely the reactive decision-making layer:

  SEARCH  -- no target position available; do nothing (hold last pose).
  ACQUIRE -- target available; command the head to look at it every step.
  LOCKED  -- angular error has stayed under tolerance for
             `lock_hold_steps` consecutive steps.

Because the vendor's IK has a small residual steady-state error (a few
degrees) even once the head is correctly aimed at the target, tolerance is
set to reflect what's actually achievable, not an idealized zero.
"""
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class GazeState(Enum):
    SEARCH = auto()
    ACQUIRE = auto()
    LOCKED = auto()


@dataclass
class GazePolicyConfig:
    # Achievable steady-state accuracy with the vendor's analytical IK is
    # ~0.15-0.17 rad (~9-10 deg) for this geometry; tolerance is set with
    # margin above that so LOCKED is reachable, not just theoretical.
    lock_tolerance_rad: float = 0.30
    lock_hold_steps: int = 15


@dataclass
class GazePolicyOutput:
    state: str
    angular_error_rad: float
    locked: bool
    command_issued: bool


class ReachyGazePolicy:
    """Reactive FSM around the vendor's look-at IK."""

    def __init__(self, cfg: GazePolicyConfig):
        self.cfg = cfg
        self.state = GazeState.SEARCH
        self._lock_counter = 0

    def reset(self) -> None:
        self.state = GazeState.SEARCH
        self._lock_counter = 0

    def step(self, target_visible: bool,
             angular_error_rad: Optional[float]) -> GazePolicyOutput:
        if not target_visible or angular_error_rad is None:
            self.state = GazeState.SEARCH
            self._lock_counter = 0
            return GazePolicyOutput(
                state=self.state.name, angular_error_rad=float("nan"),
                locked=False, command_issued=False,
            )

        if angular_error_rad <= self.cfg.lock_tolerance_rad:
            self._lock_counter += 1
        else:
            self._lock_counter = 0

        self.state = (GazeState.LOCKED if self._lock_counter >= self.cfg.lock_hold_steps
                       else GazeState.ACQUIRE)

        return GazePolicyOutput(
            state=self.state.name, angular_error_rad=angular_error_rad,
            locked=self.state == GazeState.LOCKED, command_issued=True,
        )
