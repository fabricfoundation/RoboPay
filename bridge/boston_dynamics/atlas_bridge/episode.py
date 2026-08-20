"""Engine-agnostic episode loop and metric reporting.

Both the MuJoCo and the PyBullet runners call :func:`run_episode`, so the two
engines are scored by exactly the same code and their numbers are comparable
without any per-engine bookkeeping.
"""

from __future__ import annotations

import time
from typing import Callable, Protocol

import numpy as np

from . import kinematics
from .control_core import POLICY_ID, ShelfInspectionController
from .task import EPISODE_BUDGET_S, FALL_THRESHOLD_M, INSPECTION_TARGETS

#: The task is only "done" when every target was reached and held.
REQUIRED_TARGETS = len(INSPECTION_TARGETS)

MODEL_SOURCE = "openai/roboschool @ d32bcb2 — atlas_v4_with_multisense.urdf (MIT)"


class InspectionEnvironment(Protocol):
    """The surface every simulator backend has to provide."""

    control_timestep: float
    min_pelvis_height: float
    max_end_effector_speed: float
    shelf_contacts: int
    fall_detected: bool

    def joint_limits(self) -> dict[str, tuple[float, float]]: ...
    def reset(self, joint_targets: dict[str, float]) -> dict: ...
    def step(self, joint_targets: dict[str, float]) -> dict: ...
    def safe_stop(self) -> dict: ...
    def end_effector(self) -> np.ndarray: ...
    def joint_angles(self) -> dict[str, float]: ...
    def base_rotation(self) -> np.ndarray: ...


def run_episode(
    environment: InspectionEnvironment,
    engine: str,
    max_duration_seconds: float = EPISODE_BUDGET_S,
    stop_requested: Callable[[], bool] | None = None,
    on_step: Callable[[int, dict, object], None] | None = None,
) -> dict:
    """Drive one shelf-inspection episode and return its metrics."""
    controller = ShelfInspectionController(budget_seconds=max_duration_seconds)
    observation = environment.reset(controller.reset(environment.joint_limits()))
    should_stop = stop_requested or (lambda: False)

    wall_start = time.perf_counter()
    control_steps = 0
    safe_stopped = False
    plan = None
    # The episode maximum is dominated by RETURN, where the controller commands
    # the stance pose as a step and only the actuators bound the motion. That
    # says nothing about how fast the arm moves near the shelf, so the speed
    # reached while reaching and verifying is reported separately.
    max_task_phase_speed = 0.0
    previous_hand = None

    while observation["sim_time"] < max_duration_seconds:
        if should_stop():
            observation = environment.safe_stop()
            safe_stopped = True
            break
        # The Jacobian comes from the shared URDF kinematics, not from the
        # engine, so all simulators drive the arm with identical maths.
        angles = environment.joint_angles()
        jacobian = kinematics.jacobian(angles, base_rotation=environment.base_rotation())
        plan = controller.step(
            environment.end_effector(), jacobian, observation["sim_time"], angles
        )
        phase = controller.state.phase
        observation = environment.step(plan.joint_targets)
        hand = environment.end_effector()
        if previous_hand is not None and phase in ("REACH", "VERIFY"):
            travelled = float(np.linalg.norm(hand - previous_hand))
            max_task_phase_speed = max(
                max_task_phase_speed, travelled / environment.control_timestep
            )
        previous_hand = hand
        control_steps += 1
        if on_step is not None:
            # The plan goes with it so a caller can render the decision the
            # controller just made without running its own loop to get at it —
            # a second loop would be a second episode.
            on_step(control_steps, observation, plan)
        if environment.fall_detected or controller.finished:
            break

    diagnostics = controller.diagnostics()
    completed = diagnostics["targets_completed"]
    errors = [entry["final_error_m"] for entry in diagnostics["per_target"] if entry["reached"]]

    if safe_stopped:
        completion_reason = "safe_stopped"
    elif environment.fall_detected:
        completion_reason = "fall"
    elif controller.finished:
        completion_reason = "sequence_complete"
    else:
        completion_reason = "time_limit"

    success = (
        completed == REQUIRED_TARGETS
        and not environment.fall_detected
        and environment.shelf_contacts == 0
        and not safe_stopped
    )

    return {
        "simulator_engine": engine,
        "robot_model": "Boston Dynamics Atlas v4",
        "model_source": MODEL_SOURCE,
        "base": "free-standing (no weld, no external support)",
        "task": "inspect_shelf",
        "policy_id": POLICY_ID,
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": completion_reason,
        "safe_stop_applied": safe_stopped,
        "sim_duration_seconds": round(float(observation["sim_time"]), 3),
        "wall_time_seconds": round(time.perf_counter() - wall_start, 3),
        "control_steps": control_steps,
        "targets_total": REQUIRED_TARGETS,
        "targets_completed": completed,
        "mean_position_error_m": round(sum(errors) / len(errors), 5) if errors else None,
        "max_position_error_m": round(max(errors), 5) if errors else None,
        "final_pelvis_height_m": round(float(observation["pelvis_height"]), 4),
        "min_pelvis_height_m": round(float(environment.min_pelvis_height), 4),
        "fall_threshold_m": FALL_THRESHOLD_M,
        "fall_detected": environment.fall_detected,
        "shelf_contacts": environment.shelf_contacts,
        "max_end_effector_speed_mps": round(environment.max_end_effector_speed, 4),
        "max_end_effector_speed_inspecting_mps": round(max_task_phase_speed, 4),
        "final_torso_roll_rad": round(float(observation["torso_roll"]), 4),
        "final_torso_pitch_rad": round(float(observation["torso_pitch"]), 4),
        "final_phase": plan.phase if plan else "STAND",
        "policy_state": diagnostics,
    }
