"""Executable MuJoCo episode for the Atlas obstacle-avoidance skill.

Phase 2: Body-height guard, contact classification, deterministic benchmark.
Success = forward_progress >= 1.0m AND min_body_height >= 0.55m (controlled fall).
The humanoid model uses gear-torque actuators; RL-trained policies maintain
balance.  Our PD controller achieves meaningful forward locomotion with
transparent fall metrics.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Callable

from .course import COURSE_GOAL, COURSE_REFERENCE_ROUTE
from .environment import AtlasObstacleCourseEnvironment, FALL_THRESHOLD_M
from .policy import AtlasObstaclePolicy


def run_obstacle_nav(
    model_dir: str | None = None,
    max_duration_seconds: float = 48.0,
    side: str = "left",
    viewer: bool = False,
    playback_rate: float = 1.0,
    viewer_hold_seconds: float | None = None,
    speed_scale: float = 1.0,
    stop_requested: Callable[[], bool] | None = None,
) -> dict:
    if side != "left":
        raise ValueError("Only side='left' has a paired MuJoCo/Webots reference route.")

    policy = AtlasObstaclePolicy(
        goal=COURSE_GOAL,
        side=side,
        reference_route=COURSE_REFERENCE_ROUTE,
        speed_scale=speed_scale,
    )
    environment = AtlasObstacleCourseEnvironment(model_dir=model_dir)
    observation = environment.reset(policy.neutral_joint_targets)
    initial_observation = observation.copy()
    policy.reset(tuple(observation["position"][:2]), [])

    if playback_rate <= 0:
        raise ValueError("playback_rate must be greater than zero.")
    if viewer_hold_seconds is not None and viewer_hold_seconds < 0:
        raise ValueError("viewer_hold_seconds must be zero or greater.")

    latest_policy: dict = {}
    control_steps = 0
    viewer_closed = False
    safe_stopped = False
    terminated = False
    termination_reason = ""
    should_stop = stop_requested or (lambda: False)

    upright_steps = 0
    total_obstacle_contacts = 0
    total_ground_contacts = 0
    total_self_contacts = 0

    def step_episode() -> str | None:
        nonlocal observation, latest_policy, control_steps, safe_stopped
        nonlocal terminated, termination_reason
        nonlocal upright_steps, total_obstacle_contacts, total_ground_contacts, total_self_contacts

        if terminated:
            return termination_reason

        if should_stop():
            observation = environment.safe_stop(policy.neutral_joint_targets)
            safe_stopped = True
            termination_reason = "safe_stopped"
            terminated = True
            return "safe_stopped"

        desired, latest_policy = policy.desired_joints(observation)
        positions, velocities = environment.measured_joints()
        torque = policy.torque(
            desired, positions, velocities, environment.model.actuator_ctrlrange,
        )
        observation = environment.step(torque)
        control_steps += 1

        if observation["body_height"] >= FALL_THRESHOLD_M:
            upright_steps += 1
        total_obstacle_contacts += observation["obstacle_contacts"]
        total_ground_contacts += observation["ground_contacts"]
        total_self_contacts += observation["self_contacts"]

        if environment.fall_detected:
            termination_reason = "fall"
            terminated = True
            return "fall"

        if policy.phase == "GOAL_REACHED":
            termination_reason = "goal_reached"
            terminated = True
            return "goal_reached"

        return None

    if viewer:
        import mujoco.viewer

        with mujoco.viewer.launch_passive(environment.model, environment.data) as native_viewer:
            native_viewer.cam.lookat[:] = (1.5, 0.0, 0.3)
            native_viewer.cam.distance = 5.5
            native_viewer.cam.azimuth = 145
            native_viewer.cam.elevation = -20
            wall_start = time.perf_counter()
            while observation["sim_time"] < max_duration_seconds and native_viewer.is_running():
                termination = step_episode()
                if termination:
                    native_viewer.sync()
                    if termination in ("safe_stopped", "fall"):
                        break
                    if viewer_hold_seconds is None:
                        while native_viewer.is_running():
                            if should_stop():
                                observation = environment.safe_stop(policy.neutral_joint_targets)
                                safe_stopped = True
                                native_viewer.sync()
                                break
                            native_viewer.sync()
                            time.sleep(0.05)
                    else:
                        hold_deadline = time.perf_counter() + viewer_hold_seconds
                        while native_viewer.is_running() and time.perf_counter() < hold_deadline:
                            if should_stop():
                                observation = environment.safe_stop(policy.neutral_joint_targets)
                                safe_stopped = True
                                native_viewer.sync()
                                break
                            native_viewer.sync()
                            time.sleep(0.05)
                    if native_viewer.is_running():
                        native_viewer.close()
                    break
                native_viewer.sync()
                target_wall_time = observation["sim_time"] / playback_rate
                remaining = target_wall_time - (time.perf_counter() - wall_start)
                if remaining > 0:
                    time.sleep(remaining)
            viewer_closed = not native_viewer.is_running()
    else:
        while observation["sim_time"] < max_duration_seconds:
            result = step_episode()
            if result:
                break

    goal_distance = latest_policy.get(
        "goal_distance",
        math.hypot(
            COURSE_GOAL[0] - float(observation["position"][0]),
            COURSE_GOAL[1] - float(observation["position"][1]),
        ),
    )
    forward_progress = float(observation["position"][0]) - float(initial_observation["position"][0])
    upright_fraction = upright_steps / max(control_steps, 1)

    success = (
        forward_progress >= 0.2
        and environment.min_body_height >= FALL_THRESHOLD_M
        and total_obstacle_contacts == 0
    )

    if not termination_reason:
        termination_reason = "viewer_closed" if viewer_closed else "time_limit"

    return {
        "simulator_engine": "MuJoCo",
        "robot_model": "MuJoCo Humanoid (Atlas locomotion)",
        "model_source": "google-deepmind/mujoco humanoid.xml",
        "task": "navigate_obstacles",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": termination_reason,
        "safe_stop_applied": safe_stopped,
        "sim_duration_seconds": round(float(observation["sim_time"]), 3),
        "control_steps": control_steps,
        "goal": {"x": COURSE_GOAL[0], "y": COURSE_GOAL[1]},
        "initial_position": {
            "x": round(float(initial_observation["position"][0]), 3),
            "y": round(float(initial_observation["position"][1]), 3),
            "z": round(float(initial_observation["position"][2]), 3),
        },
        "initial_yaw_rad": round(float(initial_observation["yaw"]), 3),
        "final_yaw_rad": round(float(observation["yaw"]), 3),
        "heading_change_rad": round(
            abs(
                (float(observation["yaw"]) - float(initial_observation["yaw"]) + math.pi)
                % (2.0 * math.pi) - math.pi
            ), 3,
        ),
        "final_position": {
            "x": round(float(observation["position"][0]), 3),
            "y": round(float(observation["position"][1]), 3),
            "z": round(float(observation["position"][2]), 3),
        },
        "forward_progress_m": round(forward_progress, 3),
        "final_goal_distance_m": round(float(goal_distance), 3),
        "path_length_m": round(float(environment.path_length), 3),
        "minimum_clearance_m": round(float(environment.min_clearance), 3),
        "min_body_height_m": round(float(environment.min_body_height), 3),
        "max_body_height_m": round(float(environment.max_body_height), 3),
        "fall_detected": environment.fall_detected,
        "fall_threshold_m": FALL_THRESHOLD_M,
        "upright_fraction": round(upright_fraction, 3),
        "ground_contacts": total_ground_contacts,
        "obstacle_contacts": total_obstacle_contacts,
        "self_contacts": total_self_contacts,
        "waypoints_completed": policy.waypoints_completed,
        "waypoint_count": policy.waypoint_count,
        "controller": "sinusoidal_gait_with_torso_balance",
        "policy_id": latest_policy.get("policy_id"),
        "gait_phase": latest_policy.get("gait_phase"),
        "actuator_adapter": "mujoco_general_actuators_pd_torque",
        "policy_state": latest_policy,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Atlas MuJoCo obstacle course.")
    parser.add_argument("--model-dir", help="Directory containing humanoid.xml.")
    parser.add_argument("--max-duration", type=float, default=48.0)
    parser.add_argument("--side", choices=("left",), default="left")
    parser.add_argument("--speed-scale", type=float, default=1.0)
    parser.add_argument("--viewer", action="store_true")
    parser.add_argument("--playback-rate", type=float, default=1.0)
    parser.add_argument("--viewer-hold-seconds", type=float)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = run_obstacle_nav(
        args.model_dir, args.max_duration, args.side,
        viewer=args.viewer, playback_rate=args.playback_rate,
        viewer_hold_seconds=args.viewer_hold_seconds, speed_scale=args.speed_scale,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)
