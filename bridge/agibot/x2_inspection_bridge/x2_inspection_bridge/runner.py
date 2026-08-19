"""Run the AGIBot X2 active-inspection episode in MuJoCo."""

from __future__ import annotations

import math
import time
from typing import Callable

import mujoco
import numpy as np

from .environment import X2InspectionEnvironment, MODEL_COMMIT
from .policy import X2InspectionPolicy


def run_inspection(
    model_dir: str | None = None,
    max_duration_seconds: float = 18.0,
    targets: tuple[str, ...] = ("left", "center", "right"),
    speed_scale: float = 1.0,
    viewer: bool = False,
    playback_rate: float = 1.0,
    viewer_hold_seconds: float | None = None,
    viewer_target_hold_seconds: float = 0.0,
    viewer_start_hold_seconds: float = 0.0,
    stop_requested: Callable[[], bool] | None = None,
) -> dict:
    environment = X2InspectionEnvironment(model_dir)
    observation = environment.reset()
    initial = observation
    policy = X2InspectionPolicy(targets, speed_scale)
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
            native_viewer.cam.lookat[:] = (0.15, 0.0, 0.72)
            native_viewer.cam.distance = 2.35
            native_viewer.cam.azimuth = 145
            native_viewer.cam.elevation = -10
            start_deadline = time.perf_counter() + viewer_start_hold_seconds
            while native_viewer.is_running() and time.perf_counter() < start_deadline:
                native_viewer.sync()
                time.sleep(0.05)
            started = time.perf_counter()
            # Physics remains at the official 2 ms timestep. Rendering every
            # physics step would request 500 GUI refreshes per second and can
            # stretch a short episode beyond the x402 authorization window on
            # Windows. A 60 Hz viewer is smooth while leaving all control and
            # state-integration steps intact.
            viewer_sync_period = 1.0 / 60.0
            next_viewer_sync = observation["sim_time"]
            while observation["sim_time"] < max_duration_seconds and native_viewer.is_running():
                confirmed_before_tick = len(policy.completed_targets)
                completed = tick()
                target_confirmed = len(policy.completed_targets) > confirmed_before_tick
                if completed or safe_stopped or target_confirmed or observation["sim_time"] >= next_viewer_sync:
                    native_viewer.sync()
                    next_viewer_sync = observation["sim_time"] + viewer_sync_period
                if target_confirmed and viewer_target_hold_seconds > 0:
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
        "robot_model": "AGIBot X2 Ultra v1.4 31-DoF (official MJCF)",
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
        "head_final_yaw_rad": round(float(observation["joint_positions"][15]), 4),
        "head_final_pitch_rad": round(float(observation["joint_positions"][16]), 4),
        "left_hand_motion_m": round(left_motion, 4),
        "right_hand_motion_m": round(right_motion, 4),
        "left_hand_peak_motion_m": round(environment.max_hand_motion[0], 4),
        "right_hand_peak_motion_m": round(environment.max_hand_motion[1], 4),
        "max_joint_speed_rad_s": round(environment.max_joint_speed, 4),
        "support_fixture": "pelvis safety fixture with feet on floor",
        "controller": "shared_closed_loop_target_sequencer_with_pd_torque_adapter",
        "policy_id": latest_policy.get("policy_id"),
        "policy_state": latest_policy,
    }
