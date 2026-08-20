"""Deterministic shelf-inspection controller shared by every simulator.

The controller is a state machine driving a damped-least-squares resolved-rate
loop on the right arm::

    STAND -> REACH(t) -> VERIFY(t) -> ... -> RETURN -> DONE

Nothing here is a recorded trajectory.  Each control step re-reads the measured
end-effector position and the measured arm Jacobian and solves for the next
joint increment, so the same code drives the arm to targets it has never seen
and reacts to whatever the physics engine actually does.

The module is simulator-independent: callers supply the current end-effector
position and Jacobian, and receive joint position targets back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .task import (
    EPISODE_BUDGET_S,
    INSPECTION_CHAIN,
    INSPECTION_TARGETS,
    STANCE_POSE,
    InspectionTarget,
)

POLICY_ID = "atlas-shelf-inspection-dls-v1"

#: Damping factor of the damped-least-squares pseudo-inverse.  Keeps the solve
#: well conditioned when the arm approaches a singular configuration.
DLS_DAMPING = 0.12
#: Joint-increment gain per control step.
STEP_GAIN = 0.006
#: Largest joint increment allowed in one control step, in radians.
MAX_JOINT_STEP = 0.01
#: Steps spent settling into the stance before the first reach.
STAND_SETTLE_STEPS = 400
#: Steps allowed per target before it is recorded as not reached.
REACH_TIMEOUT_STEPS = 1500
#: How far a joint target may run ahead of where the joint actually is.
#: Without this the resolved-rate loop keeps integrating while the plant lags,
#: and on a slower servo the arm overshoots its target into the shelf.
MAX_TARGET_LEAD_RAD = 0.12


@dataclass
class ControlPlan:
    """One control step's output plus the diagnostics behind it."""

    phase: str
    joint_targets: dict[str, float]
    active_target: str
    target_index: int
    position_error_m: float
    hold_progress: int
    targets_completed: int


@dataclass
class TargetOutcome:
    """Per-target result recorded when a target is left."""

    name: str
    reached: bool
    final_error_m: float
    best_error_m: float
    steps: int


@dataclass
class InspectionState:
    phase: str = "STAND"
    index: int = 0
    steps_in_phase: int = 0
    hold_counter: int = 0
    best_error: float = math.inf
    outcomes: list[TargetOutcome] = field(default_factory=list)


class ShelfInspectionController:
    """State machine plus resolved-rate IK for the Atlas shelf-inspection skill."""

    def __init__(
        self,
        targets: tuple[InspectionTarget, ...] = INSPECTION_TARGETS,
        chain: tuple[str, ...] = INSPECTION_CHAIN,
        budget_seconds: float = EPISODE_BUDGET_S,
    ) -> None:
        if not targets:
            raise ValueError("At least one inspection target is required.")
        if not chain:
            raise ValueError("The inspection chain must contain at least one joint.")
        self.targets = targets
        self.chain = chain
        self.budget_seconds = float(budget_seconds)
        self._joint_targets: dict[str, float] = dict(STANCE_POSE)
        self._limits: dict[str, tuple[float, float]] = {}
        self.state = InspectionState()

    # -- lifecycle ---------------------------------------------------------
    def reset(self, joint_limits: dict[str, tuple[float, float]]) -> dict[str, float]:
        """Start a new episode and return the initial joint targets."""
        missing = [joint for joint in self.chain if joint not in joint_limits]
        if missing:
            raise KeyError(f"Missing joint limits for {missing}")
        self._limits = dict(joint_limits)
        self._joint_targets = dict(STANCE_POSE)
        for joint in self.chain:
            self._joint_targets.setdefault(joint, 0.0)
        self.state = InspectionState()
        return dict(self._joint_targets)

    @property
    def targets_completed(self) -> int:
        return sum(1 for outcome in self.state.outcomes if outcome.reached)

    @property
    def finished(self) -> bool:
        return self.state.phase == "DONE"

    # -- control step ------------------------------------------------------
    def step(
        self,
        end_effector: np.ndarray,
        jacobian: np.ndarray,
        sim_time: float,
        joint_angles: dict[str, float] | None = None,
    ) -> ControlPlan:
        """Advance one control step.

        ``jacobian`` is the 3 x len(chain) positional Jacobian of the end
        effector with respect to the inspection chain.  ``joint_angles`` are the
        measured joint positions, used to stop the joint targets from running
        away from the joints they command.
        """
        if jacobian.shape != (3, len(self.chain)):
            raise ValueError(f"Jacobian must be 3x{len(self.chain)}, got {jacobian.shape}")

        state = self.state
        state.steps_in_phase += 1

        if state.phase == "STAND":
            if state.steps_in_phase >= STAND_SETTLE_STEPS:
                self._enter("REACH")
            return self._plan(0.0)

        if state.phase in ("RETURN", "DONE"):
            if state.phase == "RETURN" and state.steps_in_phase >= STAND_SETTLE_STEPS:
                self._enter("DONE")
            for joint, value in STANCE_POSE.items():
                self._joint_targets[joint] = value
            for joint in self.chain:
                if joint not in STANCE_POSE:
                    self._joint_targets[joint] = 0.0
            return self._plan(0.0)

        target = self.targets[state.index]
        goal = np.asarray(target.position, dtype=np.float64)
        error = goal - np.asarray(end_effector, dtype=np.float64)
        distance = float(np.linalg.norm(error))
        state.best_error = min(state.best_error, distance)

        if distance <= target.tolerance_m:
            state.hold_counter += 1
        else:
            state.hold_counter = 0

        if state.phase == "REACH":
            self._servo(jacobian, error, joint_angles)
            if state.hold_counter > 0:
                self._enter("VERIFY", keep_index=True)
        elif state.phase == "VERIFY":
            self._servo(jacobian, error, joint_angles)
            if state.hold_counter >= target.hold_steps:
                self._finish_target(target, distance, reached=True)
            elif state.hold_counter == 0:
                self._enter("REACH", keep_index=True)

        timed_out = state.steps_in_phase >= REACH_TIMEOUT_STEPS
        over_budget = sim_time >= self.budget_seconds
        if (timed_out or over_budget) and state.phase in ("REACH", "VERIFY"):
            self._finish_target(target, distance, reached=False)

        return self._plan(distance)

    # -- internals ---------------------------------------------------------
    def _servo(
        self,
        jacobian: np.ndarray,
        error: np.ndarray,
        joint_angles: dict[str, float] | None,
    ) -> None:
        """One damped-least-squares resolved-rate increment on the arm chain."""
        jjt = jacobian @ jacobian.T + (DLS_DAMPING**2) * np.eye(3)
        delta = jacobian.T @ np.linalg.solve(jjt, error)
        for index, joint in enumerate(self.chain):
            step = float(np.clip(STEP_GAIN * delta[index], -MAX_JOINT_STEP, MAX_JOINT_STEP))
            low, high = self._limits[joint]
            target = self._joint_targets[joint] + step
            if joint_angles is not None and joint in joint_angles:
                measured = joint_angles[joint]
                target = float(
                    np.clip(target, measured - MAX_TARGET_LEAD_RAD, measured + MAX_TARGET_LEAD_RAD)
                )
            self._joint_targets[joint] = float(np.clip(target, low, high))

    def _finish_target(self, target: InspectionTarget, distance: float, reached: bool) -> None:
        state = self.state
        state.outcomes.append(
            TargetOutcome(
                name=target.name,
                reached=reached,
                final_error_m=round(distance, 5),
                best_error_m=round(state.best_error, 5),
                steps=state.steps_in_phase,
            )
        )
        if state.index + 1 < len(self.targets):
            state.index += 1
            self._enter("REACH")
        else:
            self._enter("RETURN")

    def _enter(self, phase: str, keep_index: bool = False) -> None:
        self.state.phase = phase
        self.state.steps_in_phase = 0
        self.state.hold_counter = 0
        if not keep_index:
            self.state.best_error = math.inf

    def _plan(self, distance: float) -> ControlPlan:
        state = self.state
        active = (
            self.targets[state.index].name
            if state.phase in ("REACH", "VERIFY")
            else state.phase.lower()
        )
        return ControlPlan(
            phase=state.phase,
            joint_targets=dict(self._joint_targets),
            active_target=active,
            target_index=state.index,
            position_error_m=distance,
            hold_progress=state.hold_counter,
            targets_completed=self.targets_completed,
        )

    def diagnostics(self) -> dict:
        return {
            "policy_id": POLICY_ID,
            "controller": "state_machine + damped_least_squares_resolved_rate",
            "phase": self.state.phase,
            "chain": list(self.chain),
            "targets_total": len(self.targets),
            "targets_completed": self.targets_completed,
            "per_target": [
                {
                    "name": outcome.name,
                    "reached": outcome.reached,
                    "final_error_m": outcome.final_error_m,
                    "best_error_m": outcome.best_error_m,
                    "control_steps": outcome.steps,
                }
                for outcome in self.state.outcomes
            ],
            "parameters": {
                "dls_damping": DLS_DAMPING,
                "step_gain": STEP_GAIN,
                "max_joint_step_rad": MAX_JOINT_STEP,
                "reach_timeout_steps": REACH_TIMEOUT_STEPS,
            },
        }
