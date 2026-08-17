"""Measured-state X30 inspection gait using the vendor model's real joints."""

from __future__ import annotations

import math
import time
from threading import Event
from typing import Any

import numpy as np

from .course import (
    BLOCKER_CENTERS_M,
    FIRST_CLEARANCE_OFFSET_M,
    FIRST_OBSTACLE_PASSED_PROGRESS_M,
    FINISH_LINE_X_M,
    MAX_APPROACH_CLEARANCE_M,
    MIN_FORWARD_PROGRESS_M,
    SECOND_CLEARANCE_OFFSET_M,
    COURSE_ID,
    fingerprint as course_fingerprint,
    horizontal_clearance_m,
)
from .contracts import DRIVE_SKILL, DriveRequest
from .model import JOINT_NAMES, TORSO_BODY, load_mujoco_inspection_model


SETTLE_SECONDS = 1.0
TERMINAL_SETTLE_SECONDS = 0.35
FIRST_EVASION_DEADLINE_SECONDS = 30.0
FIRST_PASS_DEADLINE_SECONDS = 38.0
SECOND_EVASION_DEADLINE_SECONDS = 43.0
MIN_SAFE_BASE_HEIGHT_M = 0.30
MAX_SAFE_TILT_RAD = 0.70
MIN_JOINT_EXCURSION_RATIO = 0.55
MUJOCO_FIRST_EVASION_TARGET_M = 0.62
MUJOCO_SECOND_EVASION_TARGET_M = 0.90

# The neutral standing pose follows the twelve published X30 hip/thigh/knee
# joints in FL, FR, HL, HR order.  It is a bounded profile controller target,
# not a written base pose or kinematic replay.
STANDING_TARGET = np.asarray(
    [0.0, -0.732, 1.361, 0.0, -0.732, 1.361, 0.0, -0.732, 1.361, 0.0, -0.732, 1.361],
    dtype=float,
)
HIP_Y_INDICES = np.asarray([1, 4, 7, 10], dtype=int)
HIP_X_INDICES = np.asarray([0, 3, 6, 9], dtype=int)
KNEE_INDICES = np.asarray([2, 5, 8, 11], dtype=int)
# Mirrored diagonal patterns generate two slow physical arcs. They are joint
# targets, not written base poses: MuJoCo owns the resulting path and contacts.
FORWARD_PHASES = np.asarray([math.pi, 0.0, 0.0, math.pi], dtype=float)
LATERAL_PHASES = np.asarray([0.0, math.pi, math.pi, 0.0], dtype=float)
LATERAL_STEP_FREQUENCY_HZ = 0.65
LANE_HIP_SWEEP_MULTIPLIER = 2.0
COURSE_OBSTACLE_GEOMS = frozenset(
    f"course_blocker_{index}_collision" for index in range(1, len(BLOCKER_CENTERS_M) + 1)
)
POSITION_KP = 500.0
POSITION_KD = 20.0
TORQUE_LIMITS_NM = np.asarray([84.0, 84.0, 150.0] * 4, dtype=float)


def _joint_addresses(model: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import mujoco

    joint_ids = np.asarray(
        [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in JOINT_NAMES], dtype=int
    )
    actuator_ids = np.asarray(
        [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in JOINT_NAMES], dtype=int
    )
    if np.any(joint_ids < 0) or np.any(actuator_ids < 0):
        raise RuntimeError("X30 profile overlay did not expose each official joint actuator")
    return actuator_ids, model.jnt_qposadr[joint_ids], model.jnt_dofadr[joint_ids]


def _tilt_rad(data: Any, torso_id: int) -> float:
    rotation = np.asarray(data.xmat[torso_id]).reshape(3, 3)
    return float(math.acos(float(np.clip(rotation[2, 2], -1.0, 1.0))))


def _planar_body_axes(data: Any, torso_id: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Return the torso's world-space nose (+X), left (+Y), and yaw."""

    rotation = np.asarray(data.xmat[torso_id]).reshape(3, 3)
    forward = rotation[:2, 0]
    left = rotation[:2, 1]
    norm = float(np.linalg.norm(forward))
    if norm <= 1e-9:
        raise RuntimeError("MuJoCo returned a non-planar X30 torso orientation")
    forward = forward / norm
    return forward, left / norm, float(math.atan2(forward[1], forward[0]))


def _gait_target(
    request: DriveRequest,
    elapsed: float,
    controller_phase: str,
    phase_started_at: float,
    lateral_offset_m: float,
) -> np.ndarray:
    """Return bounded targets for the measured-state task-controller phase."""

    target = STANDING_TARGET.copy()
    if controller_phase == "settle":
        return target
    phase_age = elapsed - phase_started_at
    if controller_phase in {"evade_first", "evade_second"}:
        lateral_wave = np.sin(
            2.0 * math.pi * LATERAL_STEP_FREQUENCY_HZ * phase_age + LATERAL_PHASES
        )
        target[HIP_X_INDICES] += 0.16 * lateral_wave
        target[KNEE_INDICES] += 0.30 * np.maximum(0.0, lateral_wave)
        return target
    frequency_hz = request.gait_cycles / request.max_duration_sec
    phase = 2.0 * math.pi * frequency_hz * phase_age
    gait_phases = FORWARD_PHASES
    # A diagonal trot advances along the profile lane using only official
    # joints; MuJoCo owns base motion, contacts, and terminal state.
    swing = LANE_HIP_SWEEP_MULTIPLIER * request.hip_sweep_rad * np.sin(phase + gait_phases)
    target[HIP_Y_INDICES] += swing
    target[KNEE_INDICES] += 0.35 * np.maximum(0.0, np.cos(phase + gait_phases))
    return target


def run_drive_episode(
    request: DriveRequest,
    *,
    model_dir: str | None = None,
    stop_event: Event | None = None,
    viewer: bool = False,
    viewer_hold_seconds: float = 0.0,
) -> dict[str, Any]:
    """Run a bounded native X30 inspection gait and report measured state.

    The official model has no bundled actuator/free-base definitions.  The
    profile overlay supplies bounded position actuators and a free base, while
    MuJoCo remains the authority for every joint, torso pose, contact, and
    terminal result.  No simulator qpos/qvel is written after initialization.
    """

    if request.skill_id != DRIVE_SKILL:
        raise ValueError("run_drive_episode only accepts perform_inspection_gait")

    import mujoco

    model = load_mujoco_inspection_model(model_dir)
    data = mujoco.MjData(model)
    torso_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, TORSO_BODY)
    floor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "x30_profile_floor")
    if torso_id < 0 or floor_id < 0:
        raise RuntimeError("X30 profile overlay is missing its torso or collision floor")
    actuators, qpos_addresses, qvel_addresses = _joint_addresses(model)
    obstacle_geom_ids = {
        mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
        for name in COURSE_OBSTACLE_GEOMS
    }
    if -1 in obstacle_geom_ids or len(obstacle_geom_ids) != len(COURSE_OBSTACLE_GEOMS):
        raise RuntimeError("X30 obstacle-course overlay is missing its physical blocker collider")
    # This is the model's documented standing configuration.  It is set only
    # at episode initialization, before physics begins; no base or joint qpos
    # is overwritten after the first simulation step.
    data.qpos[qpos_addresses] = STANDING_TARGET
    free_joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "x30_profile_freejoint")
    if free_joint_id < 0:
        raise RuntimeError("X30 profile overlay is missing its free joint")
    free_qpos_address = model.jnt_qposadr[free_joint_id]
    # The official source points its nose along local +X.  This fixed initial
    # orientation makes that nose face the profile lane's global -X direction;
    # it is set once before the first physics step, never at runtime.
    data.qpos[free_qpos_address + 3:free_qpos_address + 7] = (0.0, 0.0, 0.0, 1.0)
    mujoco.mj_forward(model, data)

    state: dict[str, Any] = {
        "safe_stop_applied": False,
        "failure_reason": None,
        "min_base_height_m": float("inf"),
        "max_tilt_rad": 0.0,
        "peak_actuator_force_nm": 0.0,
        "joint_min": np.full(len(JOINT_NAMES), np.inf),
        "joint_max": np.full(len(JOINT_NAMES), -np.inf),
        "floor_contact_observed": False,
        "course_obstacle_contact_observed": False,
        "minimum_approach_clearance_m": float("inf"),
        "obstacle_approached": False,
        "trajectory": [],
        "next_trace_time": 0.0,
        "gait_started_at": None,
        "gait_completed_at": None,
        "lane_start_position": None,
        "lane_start_forward": None,
        "lane_start_left": None,
        "lane_start_yaw_rad": None,
        "max_positive_route_side_offset_m": float("-inf"),
        "min_negative_route_side_offset_m": float("inf"),
        "controller_phase": "settle",
        "phase_started_at": 0.0,
        "phase_transitions": [{"phase": "settle", "time_seconds": 0.0}],
        "task_goal_reached": False,
    }

    viewer_context = None
    if viewer:
        import mujoco.viewer

        viewer_context = mujoco.viewer.launch_passive(model, data)
        # Frame the physical blocker group and marked exercise area for a human
        # operator instead of a default camera focused on one leg.
        viewer_context.cam.lookat[:] = (-0.9, -0.1, 0.30)
        viewer_context.cam.distance = 3.4
        viewer_context.cam.azimuth = 145.0
        viewer_context.cam.elevation = -20.0

    try:
        while data.time < request.max_duration_sec:
            if stop_event is not None and stop_event.is_set():
                state["safe_stop_applied"] = True
                state["failure_reason"] = "safe_stopped"
                data.ctrl[actuators] = np.clip(
                    POSITION_KP * (STANDING_TARGET - data.qpos[qpos_addresses])
                    - POSITION_KD * data.qvel[qvel_addresses],
                    -TORQUE_LIMITS_NM,
                    TORQUE_LIMITS_NM,
                )
                break

            elapsed = float(data.time)
            if state["controller_phase"] == "settle" and elapsed >= SETTLE_SECONDS:
                state["controller_phase"] = "evade_first"
                state["phase_started_at"] = SETTLE_SECONDS
                state["phase_transitions"].append(
                    {"phase": "evade_first", "time_seconds": round(elapsed, 3)}
                )
            lateral_offset = 0.0
            if state["lane_start_position"] is not None:
                lateral_offset = float(data.xpos[torso_id][1] - state["lane_start_position"][1])
            target = _gait_target(
                request,
                elapsed,
                state["controller_phase"],
                state["phase_started_at"],
                lateral_offset,
            )
            if elapsed >= SETTLE_SECONDS and state["gait_started_at"] is None:
                state["gait_started_at"] = elapsed
                state["lane_start_position"] = np.asarray(data.xpos[torso_id], dtype=float).copy()
                forward, left, yaw = _planar_body_axes(data, torso_id)
                state["lane_start_forward"] = forward.copy()
                state["lane_start_left"] = left.copy()
                state["lane_start_yaw_rad"] = yaw
            joint_velocities = data.qvel[qvel_addresses]
            data.ctrl[actuators] = np.clip(
                POSITION_KP * (target - data.qpos[qpos_addresses]) - POSITION_KD * joint_velocities,
                -TORQUE_LIMITS_NM,
                TORQUE_LIMITS_NM,
            )
            mujoco.mj_step(model, data)
            mujoco.mj_forward(model, data)

            torso_position = np.asarray(data.xpos[torso_id], dtype=float)
            if state["lane_start_position"] is not None:
                state["max_positive_route_side_offset_m"] = max(
                    state["max_positive_route_side_offset_m"],
                    float(torso_position[1] - state["lane_start_position"][1]),
                )
                state["min_negative_route_side_offset_m"] = min(
                    state["min_negative_route_side_offset_m"],
                    float(torso_position[1] - state["lane_start_position"][1]),
                )
            tilt = _tilt_rad(data, torso_id)
            joint_positions = np.asarray(data.qpos[qpos_addresses], dtype=float)
            state["min_base_height_m"] = min(state["min_base_height_m"], float(torso_position[2]))
            state["max_tilt_rad"] = max(state["max_tilt_rad"], tilt)
            state["joint_min"] = np.minimum(state["joint_min"], joint_positions)
            state["joint_max"] = np.maximum(state["joint_max"], joint_positions)
            state["peak_actuator_force_nm"] = max(
                state["peak_actuator_force_nm"], float(np.max(np.abs(data.actuator_force[actuators])))
            )
            state["floor_contact_observed"] = state["floor_contact_observed"] or any(
                data.contact[index].geom1 == floor_id or data.contact[index].geom2 == floor_id
                for index in range(data.ncon)
            )
            state["course_obstacle_contact_observed"] = state["course_obstacle_contact_observed"] or any(
                data.contact[index].geom1 in obstacle_geom_ids or data.contact[index].geom2 in obstacle_geom_ids
                for index in range(data.ncon)
            )
            state["minimum_approach_clearance_m"] = min(
                state["minimum_approach_clearance_m"],
                horizontal_clearance_m(torso_position[:2]),
            )
            state["obstacle_approached"] = (
                state["obstacle_approached"]
                or state["minimum_approach_clearance_m"] <= MAX_APPROACH_CLEARANCE_M
            )
            if elapsed >= state["next_trace_time"]:
                state["trajectory"].append(
                    {
                        "time_seconds": round(elapsed, 3),
                        "base_position_m": [round(float(value), 5) for value in torso_position],
                    }
                )
                state["next_trace_time"] += 0.25
            if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all():
                state["failure_reason"] = "nonfinite_simulator_state"
                break
            if torso_position[2] < MIN_SAFE_BASE_HEIGHT_M or tilt > MAX_SAFE_TILT_RAD:
                state["failure_reason"] = "unsafe_simulator_state"
                break
            if state["course_obstacle_contact_observed"]:
                state["failure_reason"] = "course_obstacle_collision"
                break

            if state["lane_start_position"] is not None and state["lane_start_forward"] is not None:
                current_displacement = torso_position - state["lane_start_position"]
                current_forward_progress = float(
                    np.dot(current_displacement[:2], state["lane_start_forward"])
                )
                current_lateral_offset = float(current_displacement[1])
                if state["controller_phase"] == "evade_first":
                    if abs(current_lateral_offset) >= MUJOCO_FIRST_EVASION_TARGET_M:
                        state["controller_phase"] = "pass_first"
                        state["phase_started_at"] = elapsed
                        state["phase_transitions"].append(
                            {"phase": "pass_first", "time_seconds": round(elapsed, 3)}
                        )
                    elif elapsed >= FIRST_EVASION_DEADLINE_SECONDS:
                        state["failure_reason"] = "first_evasion_clearance_not_achieved"
                        break
                elif state["controller_phase"] == "pass_first":
                    if current_forward_progress >= FIRST_OBSTACLE_PASSED_PROGRESS_M:
                        state["controller_phase"] = "evade_second"
                        state["phase_started_at"] = elapsed
                        state["phase_transitions"].append(
                            {"phase": "evade_second", "time_seconds": round(elapsed, 3)}
                        )
                    elif elapsed >= FIRST_PASS_DEADLINE_SECONDS:
                        state["failure_reason"] = "first_obstacle_pass_timeout"
                        break
                elif state["controller_phase"] == "evade_second":
                    if abs(current_lateral_offset) >= MUJOCO_SECOND_EVASION_TARGET_M:
                        state["controller_phase"] = "pass_second"
                        state["phase_started_at"] = elapsed
                        state["phase_transitions"].append(
                            {"phase": "pass_second", "time_seconds": round(elapsed, 3)}
                        )
                    elif elapsed >= SECOND_EVASION_DEADLINE_SECONDS:
                        state["failure_reason"] = "second_evasion_clearance_not_achieved"
                        break
                goal_reached = (
                    state["controller_phase"] == "pass_second"
                    and current_forward_progress >= MIN_FORWARD_PROGRESS_M
                    and float(torso_position[0]) <= FINISH_LINE_X_M
                    and state["obstacle_approached"]
                    and max(
                        state["max_positive_route_side_offset_m"],
                        abs(state["min_negative_route_side_offset_m"]),
                    ) >= FIRST_CLEARANCE_OFFSET_M
                    and max(
                        state["max_positive_route_side_offset_m"],
                        abs(state["min_negative_route_side_offset_m"]),
                    ) >= SECOND_CLEARANCE_OFFSET_M
                )
                if goal_reached:
                    state["controller_phase"] = "goal_hold"
                    state["phase_transitions"].append(
                        {"phase": "goal_hold", "time_seconds": round(elapsed, 3)}
                    )
                    state["task_goal_reached"] = True
                    state["gait_completed_at"] = elapsed
                    break

            if elapsed >= request.max_duration_sec - TERMINAL_SETTLE_SECONDS:
                state["failure_reason"] = "task_goal_timeout"
                state["gait_completed_at"] = elapsed
                break
            if viewer_context is not None:
                viewer_context.sync()
                time.sleep(model.opt.timestep)
    finally:
        # Keep applying physical stand torques while the final state settles;
        # releasing every motor here would deliberately make a sound robot
        # collapse after a successful episode.
        for _ in range(max(1, int(TERMINAL_SETTLE_SECONDS / model.opt.timestep))):
            data.ctrl[actuators] = np.clip(
                POSITION_KP * (STANDING_TARGET - data.qpos[qpos_addresses])
                - POSITION_KD * data.qvel[qvel_addresses],
                -TORQUE_LIMITS_NM,
                TORQUE_LIMITS_NM,
            )
            mujoco.mj_step(model, data)

    mujoco.mj_forward(model, data)
    torso_position = np.asarray(data.xpos[torso_id], dtype=float)
    final_forward, final_left, final_yaw = _planar_body_axes(data, torso_id)
    lane_start = state["lane_start_position"]
    lane_displacement = torso_position - lane_start if lane_start is not None else np.zeros(3)
    lane_progress = float(-lane_displacement[0])
    lateral_drift = float(abs(lane_displacement[1]))
    start_forward = state["lane_start_forward"]
    start_left = state["lane_start_left"]
    start_yaw = state["lane_start_yaw_rad"]
    if start_forward is None or start_left is None or start_yaw is None:
        body_forward_progress = 0.0
        heading_course_alignment = -1.0
        max_positive_route_side_offset_m = 0.0
        min_negative_route_side_offset_m = 0.0
    else:
        body_forward_progress = float(np.dot(lane_displacement[:2], start_forward))
        heading_course_alignment = float(np.dot(start_forward, np.asarray([-1.0, 0.0])))
        max_positive_route_side_offset_m = max(
            float(state["max_positive_route_side_offset_m"]),
            float(lane_displacement[1]),
        )
        min_negative_route_side_offset_m = min(
            float(state["min_negative_route_side_offset_m"]),
            float(lane_displacement[1]),
        )
    finish_line_crossed = float(torso_position[0]) <= FINISH_LINE_X_M
    joint_positions = np.asarray(data.qpos[qpos_addresses], dtype=float)
    joint_excursion = state["joint_max"] - state["joint_min"]
    gait_excursion = float(np.max(joint_excursion[HIP_Y_INDICES]))
    success = (
        state["failure_reason"] is None
        and state["task_goal_reached"]
        and state["gait_completed_at"] is not None
        and gait_excursion >= request.hip_sweep_rad * MIN_JOINT_EXCURSION_RATIO
        and heading_course_alignment >= 0.95
        and body_forward_progress >= MIN_FORWARD_PROGRESS_M
        and max(max_positive_route_side_offset_m, abs(min_negative_route_side_offset_m))
        >= FIRST_CLEARANCE_OFFSET_M
        and max(max_positive_route_side_offset_m, abs(min_negative_route_side_offset_m))
        >= SECOND_CLEARANCE_OFFSET_M
        and finish_line_crossed
        and state["obstacle_approached"]
        and not state["course_obstacle_contact_observed"]
        and state["min_base_height_m"] >= MIN_SAFE_BASE_HEIGHT_M
        and state["max_tilt_rad"] <= MAX_SAFE_TILT_RAD
        and float(torso_position[2]) >= MIN_SAFE_BASE_HEIGHT_M
        and bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all())
    )
    if viewer_context is not None:
        hold_deadline = time.monotonic() + max(0.0, viewer_hold_seconds)
        while viewer_context.is_running() and time.monotonic() < hold_deadline:
            viewer_context.sync()
            time.sleep(0.02)
        viewer_context.close()
    return {
        "simulator_engine": "MuJoCo",
        "robot_model": "DeepRobotics X30 official MJCF with documented profile-owned free-base and joint-actuation overlay (X30 Pro kinematic simulation)",
        "task": DRIVE_SKILL,
        "course_id": COURSE_ID,
        "course_hash": course_fingerprint(),
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": "inspection_lane_complete" if success else state["failure_reason"] or "insufficient_course_completion",
        "task_goal_reached": state["task_goal_reached"],
        "controller_phase_transitions": state["phase_transitions"],
        "safe_stop_applied": state["safe_stop_applied"],
        "sim_duration_seconds": round(float(data.time), 4),
        "requested_gait_cycles": request.gait_cycles,
        "requested_hip_sweep_rad": request.hip_sweep_rad,
        "requested_max_duration_sec": request.max_duration_sec,
        "measured_hip_sweep_rad": round(gait_excursion, 5),
        "final_torso_position_m": [round(float(value), 5) for value in torso_position],
        "measured_lane_displacement_m": [round(float(value), 5) for value in lane_displacement],
        "measured_lane_progress_m": round(lane_progress, 5),
        "measured_lateral_drift_m": round(lateral_drift, 5),
        "max_positive_route_side_offset_m": round(max_positive_route_side_offset_m, 5),
        "min_negative_route_side_offset_m": round(min_negative_route_side_offset_m, 5),
        "body_forward_progress_m": round(body_forward_progress, 5),
        "body_heading_course_alignment": round(heading_course_alignment, 5),
        "finish_line_crossed": finish_line_crossed,
        "body_forward_world": None if start_forward is None else [round(float(value), 5) for value in start_forward],
        "body_left_world": None if start_left is None else [round(float(value), 5) for value in start_left],
        "body_yaw_rad": None if start_yaw is None else round(float(start_yaw), 5),
        "final_body_forward_world": [round(float(value), 5) for value in final_forward],
        "final_body_left_world": [round(float(value), 5) for value in final_left],
        "final_body_yaw_rad": round(float(final_yaw), 5),
        "final_joint_positions_rad": [round(float(value), 5) for value in joint_positions],
        "min_base_height_m": round(float(state["min_base_height_m"]), 5),
        "max_tilt_rad": round(float(state["max_tilt_rad"]), 5),
        "peak_actuator_force_nm": round(float(state["peak_actuator_force_nm"]), 5),
        "floor_contact_observed": state["floor_contact_observed"],
        "course": {
            "course_id": COURSE_ID,
            "course_hash": course_fingerprint(),
            "blocker_centers_m": [list(center) for center in BLOCKER_CENTERS_M],
            "physical_blockers": sorted(COURSE_OBSTACLE_GEOMS),
            "obstacle_collision_observed": state["course_obstacle_contact_observed"],
            "obstacle_approached": state["obstacle_approached"],
            "minimum_approach_clearance_m": round(float(state["minimum_approach_clearance_m"]), 5),
            "finish_line_x_m": FINISH_LINE_X_M,
            "success_requires": "body-forward travel, two measured widening-arc evasion gates, finish-line crossing, physical-course approach, and no blocker contact",
        },
        "measured_trajectory": state["trajectory"],
        "finite_state": bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()),
        "controller": "measured_state_two_obstacle_slalom_controller_with_bounded_joint_gaits",
        "state_authority": "MuJoCo official-joint qpos, actuator force, torso pose, contact state, and finite dynamic state",
        "viewer_enabled": viewer,
    }
