"""Measured-state dynamic-obstacle navigation for the vendor M20 model."""

from __future__ import annotations

import math
import time
from threading import Event
from typing import Any

import numpy as np

from .contracts import DRIVE_SKILL, DriveRequest
from .model import (
    OBSTACLE_CENTER_X_M,
    OBSTACLE_CENTER_Y_M,
    OBSTACLE_CLEAR_Y_M,
    OBSTACLE_HALF_LENGTH_M,
    OBSTACLE_HALF_WIDTH_M,
    load_mujoco_obstacle_course_model,
)


LEG_JOINTS = tuple(
    f"{leg}_{joint}_joint"
    for leg in ("fl", "fr", "hl", "hr")
    for joint in ("hipx", "hipy", "knee")
)
WHEEL_JOINTS = tuple(f"{leg}_wheel_joint" for leg in ("fl", "fr", "hl", "hr"))

STANCE_KP = 70.0
STANCE_KD = 6.0
LEG_TORQUE_LIMIT_NM = 76.4
WHEEL_SPEED_KP = 2.0
WHEEL_TORQUE_LIMIT_NM = 8.0
WHEEL_BRAKE_GAIN = 2.0
WHEEL_BRAKE_LIMIT_NM = 8.0
SETTLE_SECONDS = 1.0
OBSTACLE_YIELD_RANGE_M = 0.75
OBSTACLE_YIELD_SECONDS = 2.0
OBSTACLE_CLEAR_MOVE_SECONDS = 1.2
TERMINAL_SETTLE_SECONDS = 0.4
# Cross the public goal by a small physical margin before braking. This keeps
# the terminal, post-settle measured position beyond the requested boundary.
GOAL_COMPLETION_MARGIN_M = 0.03
MIN_SAFE_BASE_HEIGHT_M = 0.45
MAX_SAFE_TILT_RAD = 0.35


def _joint_addresses(model: Any, names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resolve actuator, qpos, and qvel addresses from locked vendor names."""

    import mujoco

    joint_ids = np.asarray(
        [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name) for name in names], dtype=int
    )
    actuator_ids = np.asarray(
        [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in names], dtype=int
    )
    if np.any(joint_ids < 0) or np.any(actuator_ids < 0):
        raise RuntimeError("Pinned M20 model is missing an expected joint or motor")
    return actuator_ids, model.jnt_qposadr[joint_ids], model.jnt_dofadr[joint_ids]


def _tilt_rad(data: Any, base_body_id: int) -> float:
    rotation = np.asarray(data.xmat[base_body_id]).reshape(3, 3)
    return float(math.acos(float(np.clip(rotation[2, 2], -1.0, 1.0))))


def _yaw_rad(data: Any, base_body_id: int) -> float:
    rotation = np.asarray(data.xmat[base_body_id]).reshape(3, 3)
    return float(math.atan2(rotation[1, 0], rotation[0, 0]))


def _course_forward_clearance_m(
    position: np.ndarray, yaw: float, obstacle_position: np.ndarray
) -> float | None:
    """Measure clearance to the course geometry from live simulator state.

    This deliberately follows the Spot profile's pose-feedback pattern: it is
    not an emulated LiDAR or a claim about an M20 Pro sensor.  The policy sees
    the measured base pose and profile-owned physical obstacle position; real
    MuJoCo contact remains the authority for collision.
    """

    dx = float(obstacle_position[0] - position[0])
    dy = float(obstacle_position[1] - position[1])
    forward = math.cos(yaw) * dx + math.sin(yaw) * dy
    lateral = -math.sin(yaw) * dx + math.cos(yaw) * dy
    if forward <= -OBSTACLE_HALF_LENGTH_M or abs(lateral) > OBSTACLE_HALF_WIDTH_M + 0.18:
        return None
    return max(0.0, forward - OBSTACLE_HALF_LENGTH_M)


def _obstacle_contact(data: Any, obstacle_geom_id: int) -> bool:
    return any(
        data.contact[index].geom1 == obstacle_geom_id or data.contact[index].geom2 == obstacle_geom_id
        for index in range(data.ncon)
    )


def run_drive_episode(
    request: DriveRequest,
    *,
    model_dir: str | None = None,
    stop_event: Event | None = None,
    viewer: bool = False,
    viewer_hold_seconds: float = 0.0,
) -> dict[str, Any]:
    """Safely yield to a physical moving obstacle, then resume to the goal.

    The robot is a real free body in MuJoCo. Only its locked vendor leg/wheel
    motor controls are written. The obstacle is a profile-owned *environment*
    mocap actor; moving it never overwrites robot qpos, qvel, pose or joints.
    Success requires measured obstacle detection, a zero-wheel-control yield,
    course release, collision-free measured state, and a goal crossing.
    """

    if request.skill_id != DRIVE_SKILL:
        raise ValueError("run_drive_episode only accepts the registered navigation skill")

    import mujoco

    model = load_mujoco_obstacle_course_model(model_dir)
    data = mujoco.MjData(model)
    base_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
    obstacle_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "obstacle_course_marker")
    obstacle_geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "course_obstacle")
    if min(base_body_id, obstacle_body_id, obstacle_geom_id) < 0:
        raise RuntimeError("M20 course is missing base_link or its physical obstacle")
    obstacle_mocap_id = int(model.body_mocapid[obstacle_body_id])
    if obstacle_mocap_id < 0:
        raise RuntimeError("M20 course obstacle must be a mocap environment body")

    leg_actuators, leg_qpos, leg_qvel = _joint_addresses(model, LEG_JOINTS)
    wheel_actuators, _, wheel_qvel = _joint_addresses(model, WHEEL_JOINTS)
    desired_stance = np.zeros(len(LEG_JOINTS), dtype=float)
    obstacle_position = np.asarray([OBSTACLE_CENTER_X_M, OBSTACLE_CENTER_Y_M, 0.0], dtype=float)
    data.mocap_pos[obstacle_mocap_id] = obstacle_position
    mujoco.mj_forward(model, data)

    state: dict[str, Any] = {
        "phase": "settling",
        "phase_history": ["settling"],
        "start_x": None,
        "start_y": None,
        "target_reached_at": None,
        "yield_started_at": None,
        "resumed_at": None,
        "safe_stop_applied": False,
        "failure_reason": None,
        "min_base_height_m": float("inf"),
        "max_tilt_rad": 0.0,
        "peak_commanded_torque_nm": 0.0,
        "obstacle_detected": False,
        "minimum_obstacle_clearance_m": float("inf"),
        "collision_detected": False,
        "obstacle_released": False,
    }

    def change_phase(phase: str) -> None:
        if state["phase"] != phase:
            state["phase"] = phase
            state["phase_history"].append(phase)

    def update_environment() -> None:
        """Move only the external course actor after the measured yield."""

        yield_started_at = state["yield_started_at"]
        if yield_started_at is not None:
            elapsed = float(data.time - yield_started_at)
            progress = np.clip(
                (elapsed - OBSTACLE_YIELD_SECONDS) / OBSTACLE_CLEAR_MOVE_SECONDS,
                0.0,
                1.0,
            )
            obstacle_position[1] = OBSTACLE_CENTER_Y_M + float(progress) * OBSTACLE_CLEAR_Y_M
            state["obstacle_released"] = bool(progress >= 1.0)
        data.mocap_pos[obstacle_mocap_id] = obstacle_position
        mujoco.mj_forward(model, data)

    def apply_control(mode: str) -> None:
        leg_torque = np.clip(
            STANCE_KP * (desired_stance - data.qpos[leg_qpos]) - STANCE_KD * data.qvel[leg_qvel],
            -LEG_TORQUE_LIMIT_NM,
            LEG_TORQUE_LIMIT_NM,
        )
        data.ctrl[:] = 0.0
        data.ctrl[leg_actuators] = leg_torque
        if mode == "cruise":
            targets = np.full(len(wheel_actuators), -request.wheel_speed_rad_s)
            data.ctrl[wheel_actuators] = np.clip(
                WHEEL_SPEED_KP * (targets - data.qvel[wheel_qvel]),
                -WHEEL_TORQUE_LIMIT_NM,
                WHEEL_TORQUE_LIMIT_NM,
            )
        elif mode in {"yield", "terminal_settle"}:
            data.ctrl[wheel_actuators] = np.clip(
                -WHEEL_BRAKE_GAIN * data.qvel[wheel_qvel],
                -WHEEL_BRAKE_LIMIT_NM,
                WHEEL_BRAKE_LIMIT_NM,
            )
        state["peak_commanded_torque_nm"] = max(
            state["peak_commanded_torque_nm"], float(np.max(np.abs(data.ctrl)))
        )

    def observe() -> tuple[np.ndarray, float, float, float | None]:
        mujoco.mj_forward(model, data)
        position = np.asarray(data.xpos[base_body_id], dtype=float)
        base_height = float(position[2])
        tilt = _tilt_rad(data, base_body_id)
        state["min_base_height_m"] = min(state["min_base_height_m"], base_height)
        state["max_tilt_rad"] = max(state["max_tilt_rad"], tilt)
        distance = 0.0 if state["start_x"] is None else float(position[0] - state["start_x"])
        clearance_m = _course_forward_clearance_m(
            position, _yaw_rad(data, base_body_id), obstacle_position
        )
        if clearance_m is not None:
            state["obstacle_detected"] = True
            state["minimum_obstacle_clearance_m"] = min(
                state["minimum_obstacle_clearance_m"], clearance_m
            )
        state["collision_detected"] = state["collision_detected"] or _obstacle_contact(data, obstacle_geom_id)
        return position, distance, tilt, clearance_m

    viewer_context = None
    if viewer:
        import mujoco.viewer

        viewer_context = mujoco.viewer.launch_passive(model, data)

    try:
        while data.time < request.max_duration_sec:
            update_environment()
            if stop_event is not None and stop_event.is_set():
                state["safe_stop_applied"] = True
                state["failure_reason"] = "safe_stopped"
                break

            position, distance, tilt, clearance_m = observe()
            if not np.isfinite(data.qpos).all() or position[2] < MIN_SAFE_BASE_HEIGHT_M or tilt > MAX_SAFE_TILT_RAD:
                state["failure_reason"] = "unsafe_or_nonfinite_simulator_state"
                break
            if state["collision_detected"]:
                state["failure_reason"] = "course_obstacle_collision"
                break

            mode = "settling"
            if data.time >= SETTLE_SECONDS:
                if state["start_x"] is None:
                    state["start_x"] = float(position[0])
                    state["start_y"] = float(position[1])
                    change_phase("approach")
                if (
                    state["phase"] == "approach"
                    and clearance_m is not None
                    and clearance_m <= OBSTACLE_YIELD_RANGE_M
                ):
                    state["yield_started_at"] = float(data.time)
                    change_phase("yield")
                if state["phase"] == "yield" and state["obstacle_released"]:
                    state["resumed_at"] = float(data.time)
                    change_phase("resume")
                if state["phase"] in {"approach", "resume"}:
                    mode = "cruise"
                elif state["phase"] == "yield":
                    mode = "yield"
                if (
                    state["phase"] == "resume"
                    and distance >= request.goal_distance_m + GOAL_COMPLETION_MARGIN_M
                    and abs(float(position[1] - state["start_y"])) <= 0.18
                ):
                    state["target_reached_at"] = float(data.time)
                    change_phase("terminal_settle")
                    mode = "terminal_settle"

            apply_control(mode)
            mujoco.mj_step(model, data)
            if state["target_reached_at"] is not None and data.time - state["target_reached_at"] >= TERMINAL_SETTLE_SECONDS:
                break
            if viewer_context is not None:
                viewer_context.sync()
                time.sleep(model.opt.timestep)
    except Exception:
        if viewer_context is not None:
            viewer_context.close()
        raise

    data.ctrl[:] = 0.0
    for _ in range(max(1, int(0.1 / model.opt.timestep))):
        mujoco.mj_step(model, data)
    position, distance, _, _ = observe()
    if viewer_context is not None:
        hold_deadline = time.monotonic() + max(0.0, viewer_hold_seconds)
        while viewer_context.is_running() and time.monotonic() < hold_deadline:
            viewer_context.sync()
            time.sleep(0.02)
        viewer_context.close()
    success = (
        state["failure_reason"] is None
        and state["target_reached_at"] is not None
        and distance >= request.goal_distance_m
        and state["obstacle_detected"]
        and state["yield_started_at"] is not None
        and state["obstacle_released"]
        and not state["collision_detected"]
        and state["min_base_height_m"] >= MIN_SAFE_BASE_HEIGHT_M
        and state["max_tilt_rad"] <= MAX_SAFE_TILT_RAD
    )
    yield_duration = None
    if state["yield_started_at"] is not None and state["resumed_at"] is not None:
        yield_duration = round(float(state["resumed_at"] - state["yield_started_at"]), 4)
    return {
        "simulator_engine": "MuJoCo",
        "robot_model": "DeepRobotics M20 official low-resolution MJCF with profile-owned physical moving obstacle course (Lynx M20 Pro kinematic base)",
        "task": DRIVE_SKILL,
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": "dynamic_obstacle_course_complete" if success else state["failure_reason"] or "timeout",
        "safe_stop_applied": state["safe_stop_applied"],
        "sim_duration_seconds": round(float(data.time), 4),
        "requested_goal_distance_m": request.goal_distance_m,
        "requested_wheel_speed_rad_s": request.wheel_speed_rad_s,
        "requested_max_duration_sec": request.max_duration_sec,
        "measured_forward_distance_m": round(distance, 5),
        "initial_course_position_m": [state["start_x"], state["start_y"]],
        "final_base_position_m": [round(float(value), 5) for value in position],
        "final_base_yaw_rad": round(_yaw_rad(data, base_body_id), 5),
        "min_base_height_m": round(float(state["min_base_height_m"]), 5),
        "max_tilt_rad": round(float(state["max_tilt_rad"]), 5),
        "peak_commanded_torque_nm": round(float(state["peak_commanded_torque_nm"]), 5),
        "finite_state": bool(np.isfinite(data.qpos).all() and np.isfinite(data.qvel).all()),
        "controller": "pose_feedback_course_clearance_yield_then_resume_wheel_torque_with_leg_pd_stance",
        "state_authority": "MuJoCo base_link pose, wheel velocity, contact state, and physical course geometry",
        "course": {
            "physical_obstacle": "course_obstacle",
            "obstacle_motion": "mocap environment actor moves laterally only after measured yield",
            "perception": "pose-feedback clearance from measured base pose to profile-owned physical obstacle",
            "obstacle_detected": state["obstacle_detected"],
            "minimum_obstacle_clearance_m": None
            if not math.isfinite(state["minimum_obstacle_clearance_m"])
            else round(float(state["minimum_obstacle_clearance_m"]), 5),
            "yield_duration_seconds": yield_duration,
            "obstacle_released": state["obstacle_released"],
            "collision_detected": state["collision_detected"],
            "phase_history": state["phase_history"],
        },
        "viewer_enabled": viewer,
    }
