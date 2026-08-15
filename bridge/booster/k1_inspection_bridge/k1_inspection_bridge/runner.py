"""Run the Booster K1 active-inspection episode in MuJoCo."""

from __future__ import annotations

import math
import time
from typing import Callable

import mujoco
import numpy as np

from .environment import K1InspectionEnvironment, MODEL_COMMIT
from .policy import K1InspectionPolicy


def run_inspection(
    model_dir: str | None = None,
    max_duration_seconds: float = 18.0,
    targets: tuple[str, ...] = ("left", "center", "right"),
    speed_scale: float = 1.0,
    viewer: bool = False,
    playback_rate: float = 1.0,
    viewer_hold_seconds: float | None = None,
    viewer_target_hold_seconds: float = 0.0,
    stop_requested: Callable[[], bool] | None = None,
) -> dict:
    environment = K1InspectionEnvironment(model_dir)
    observation = environment.reset()
    initial = observation
    policy = K1InspectionPolicy(targets, speed_scale)
    policy.reset()
    should_stop = stop_requested or (lambda: False)
    latest_policy: dict = {}
    steps = 0
    safe_stopped = False

    def tick() -> bool:
        nonlocal observation, latest_policy, steps, safe_stopped
        if should_stop():
            torque = policy.safe_stop_control(observation["joint_positions"], observation["joint_velocities"])
            observation = environment.safe_stop(torque)
            safe_stopped = True
            return True
        torque, latest_policy = policy.compute_control(observation)
        observation = environment.step(torque)
        steps += 1
        return policy.phase == "COMPLETE"

    if viewer:
        from mujoco import viewer as mujoco_viewer

        with mujoco_viewer.launch_passive(environment.model, environment.data) as native_viewer:
            native_viewer.cam.lookat[:] = (0.1, 0.0, 0.75)
            native_viewer.cam.distance = 2.5
            native_viewer.cam.azimuth = 145
            native_viewer.cam.elevation = -12
            started = time.perf_counter()
            while observation["sim_time"] < max_duration_seconds and native_viewer.is_running():
                confirmed_before_tick = len(policy.completed_targets)
                completed = tick()
                native_viewer.sync()
                if len(policy.completed_targets) > confirmed_before_tick and viewer_target_hold_seconds > 0:
                    target_deadline = time.perf_counter() + viewer_target_hold_seconds
                    while native_viewer.is_running() and time.perf_counter() < target_deadline:
                        if should_stop():
                            torque = policy.safe_stop_control(observation["joint_positions"], observation["joint_velocities"])
                            observation = environment.safe_stop(torque)
                            safe_stopped = True
                            native_viewer.sync()
                            break
                        native_viewer.sync()
                        time.sleep(0.05)
                if completed or safe_stopped:
                    native_viewer.sync()
                    deadline = None if viewer_hold_seconds is None else time.perf_counter() + viewer_hold_seconds
                    while native_viewer.is_running() and (deadline is None or time.perf_counter() < deadline):
                        if should_stop():
                            torque = policy.safe_stop_control(observation["joint_positions"], observation["joint_velocities"])
                            observation = environment.safe_stop(torque)
                            safe_stopped = True
                            native_viewer.sync()
                            break
                        native_viewer.sync()
                        time.sleep(0.05)
                    native_viewer.close()
                    break
                delay = observation["sim_time"] / playback_rate - (time.perf_counter() - started)
                if delay > 0:
                    time.sleep(delay)
    else:
        while observation["sim_time"] < max_duration_seconds:
            if tick():
                break

    left_motion = float(np.linalg.norm(observation["left_hand_position"] - initial["left_hand_position"]))
    right_motion = float(np.linalg.norm(observation["right_hand_position"] - initial["right_hand_position"]))
    completed = list(policy.completed_targets)
    success = policy.phase == "COMPLETE" and completed == list(targets) and not safe_stopped
    return {
        "simulator_engine": "MuJoCo",
        "simulator_version": mujoco.__version__,
        "robot_model": "Booster Robotics K1 22-DoF (official MJCF)",
        "model_source_commit": MODEL_COMMIT,
        "task": "inspect_target_sequence",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": "safe_stopped" if safe_stopped else "targets_confirmed" if success else "time_limit",
        "safe_stop_applied": safe_stopped,
        "sim_duration_seconds": round(float(observation["sim_time"]), 3),
        "control_steps": steps,
        "targets_requested": list(targets),
        "targets_confirmed": completed,
        "target_confirmations": list(policy.target_confirmations),
        "head_final_yaw_rad": round(float(observation["joint_positions"][0]), 4),
        "head_final_pitch_rad": round(float(observation["joint_positions"][1]), 4),
        "left_hand_motion_m": round(left_motion, 4),
        "right_hand_motion_m": round(right_motion, 4),
        "left_hand_peak_motion_m": round(environment.max_hand_motion[0], 4),
        "right_hand_peak_motion_m": round(environment.max_hand_motion[1], 4),
        "max_joint_speed_rad_s": round(environment.max_joint_speed, 4),
        "support_fixture": "fixed-base safety stand",
        "controller": "shared_closed_loop_target_sequencer_with_pd_torque_adapter",
        "policy_id": latest_policy.get("policy_id"),
        "policy_state": latest_policy,
    }
