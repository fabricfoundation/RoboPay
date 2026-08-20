"""Real MuJoCo runtime using the pinned LimX WF_TRON2A policy."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from .contracts import NAVIGATION_SKILL, STOP_SKILL, NavigationRequest
from .course import GOAL, OBSTACLES, WAYPOINTS, RoutePlanner, obstacle_clearance
from .model import build_mujoco_scene_xml
from .policy import JOINT_NAMES, LimXOnnxPolicy, quaternion_matrix


def _orientation(quat_wxyz: np.ndarray) -> tuple[float, float, float]:
    matrix = quaternion_matrix(quat_wxyz)
    pitch = math.asin(max(-1.0, min(1.0, -matrix[2, 0])))
    roll = math.atan2(matrix[2, 1], matrix[2, 2])
    yaw = math.atan2(matrix[1, 0], matrix[0, 0])
    return roll, pitch, yaw


def _contact_with_obstacle(model, data) -> bool:
    import mujoco

    for index in range(data.ncon):
        contact = data.contact[index]
        names = {
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(contact.geom1)) or "",
            mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(contact.geom2)) or "",
        }
        if any(name.startswith("course_") for name in names):
            return True
    return False


def run_mujoco_episode(
    request: NavigationRequest,
    *,
    viewer: bool = False,
    viewer_hold_seconds: float = 0.0,
) -> dict[str, Any]:
    if request.skill_id == STOP_SKILL:
        return {
            "success": True,
            "skill": STOP_SKILL,
            "message": "safe stop acknowledged; zero velocity command retained",
            "simulator": "mujoco",
            "stopped": True,
        }
    if request.skill_id != NAVIGATION_SKILL:
        raise ValueError("unsupported runtime skill")

    import mujoco

    model = mujoco.MjModel.from_xml_string(build_mujoco_scene_xml())
    data = mujoco.MjData(model)
    joint_qpos = [int(model.jnt_qposadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)]) for name in JOINT_NAMES]
    joint_dof = [int(model.jnt_dofadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)]) for name in JOINT_NAMES]
    mujoco.mj_forward(model, data)

    policy = LimXOnnxPolicy()
    planner = RoutePlanner()
    detected: set[str] = set()
    min_clearance = float("inf")
    collision = False
    max_tilt = 0.0
    path_length = 0.0
    last_position = np.array(data.qpos[:2], dtype=np.float64)
    last_actions = np.zeros(10, dtype=np.float64)
    terminal_reason = "timeout"
    window = None
    start_wall = time.monotonic()
    if viewer:
        import mujoco.viewer

        window = mujoco.viewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False)
        window.cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
        window.cam.trackbodyid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base_Link")
        window.cam.distance = 5.2
        window.cam.azimuth = 135.0
        window.cam.elevation = -24.0

    max_steps = int(request.max_duration_sec / model.opt.timestep)
    try:
        for step in range(max_steps):
            q = np.asarray([data.qpos[address] for address in joint_qpos], dtype=np.float64)
            dq = np.asarray([data.qvel[address] for address in joint_dof], dtype=np.float64)
            quat = np.asarray(data.qpos[3:7], dtype=np.float64)
            roll, pitch, yaw = _orientation(quat)
            max_tilt = max(max_tilt, abs(roll), abs(pitch))
            x, y, z = (float(data.qpos[index]) for index in range(3))
            position = np.array([x, y])
            path_length += float(np.linalg.norm(position - last_position))
            last_position = position

            for obstacle in OBSTACLES:
                clearance = obstacle_clearance(x, y, obstacle)
                min_clearance = min(min_clearance, clearance)
                if math.hypot(x - obstacle.x, y - obstacle.y) <= 1.45:
                    detected.add(obstacle.name)
            collision = collision or _contact_with_obstacle(model, data) or min_clearance < -0.02
            if collision:
                terminal_reason = "collision"
                break
            if not np.all(np.isfinite(data.qpos)) or not np.all(np.isfinite(data.qvel)):
                terminal_reason = "non_finite_state"
                break
            if step > 1200 and (z < 0.42 or max(abs(roll), abs(pitch)) > 1.05):
                terminal_reason = "unsafe_base_state"
                break

            command = (0.0, 0.0, 0.0) if step < 1800 else planner.command(x, y, yaw)
            if step % 20 == 0:
                gyro_sensor = np.asarray(data.sensor("base_imu_gyro").data, dtype=np.float64)
                last_actions = policy.actions(q, dq, quat, gyro_sensor, command)
            torques = policy.action_torques(last_actions, q, dq)
            data.ctrl[:] = torques
            mujoco.mj_step(model, data)

            if planner.complete and math.hypot(x - GOAL[0], y - GOAL[1]) <= 0.34:
                terminal_reason = "goal_reached"
                break
            if window is not None and step % 16 == 0:
                if not window.is_running():
                    terminal_reason = "viewer_closed"
                    break
                window.sync()
                target_wall = start_wall + model.opt.timestep * step
                delay = target_wall - time.monotonic()
                if delay > 0:
                    time.sleep(delay)

        final_x, final_y, final_z = (float(data.qpos[index]) for index in range(3))
        final_roll, final_pitch, final_yaw = _orientation(np.asarray(data.qpos[3:7]))
        goal_distance = math.hypot(final_x - GOAL[0], final_y - GOAL[1])
        success = (
            terminal_reason == "goal_reached"
            and not collision
            and goal_distance <= 0.34
            and final_z >= 0.42
            and max(abs(final_roll), abs(final_pitch)) <= 0.75
            and len(detected) == len(OBSTACLES)
        )
        result = {
            "success": success,
            "skill": NAVIGATION_SKILL,
            "simulator": "mujoco",
            "model_variant": "WF_TRON2A",
            "low_level_controller": "limx-isaacgym-onnx-policy",
            "terminal_reason": terminal_reason,
            "waypoints_completed": len(planner.visited),
            "waypoints_total": len(WAYPOINTS),
            "detected_obstacles": sorted(detected),
            "collision": collision,
            "minimum_clearance_m": round(float(min_clearance), 4),
            "path_length_m": round(path_length, 4),
            "goal_distance_m": round(goal_distance, 4),
            "final_base_pose": {
                "x": round(final_x, 4),
                "y": round(final_y, 4),
                "z": round(final_z, 4),
                "roll": round(final_roll, 4),
                "pitch": round(final_pitch, 4),
                "yaw": round(final_yaw, 4),
            },
            "max_tilt_rad": round(max_tilt, 4),
        }
        if not success:
            result["error_code"] = "COURSE_NOT_COMPLETED"
        if window is not None and viewer_hold_seconds > 0:
            deadline = time.monotonic() + viewer_hold_seconds
            while window.is_running() and time.monotonic() < deadline:
                window.sync()
                time.sleep(0.03)
        return result
    finally:
        if window is not None:
            window.close()
