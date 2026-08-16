"""Executable MuJoCo episode for the Go2 obstacle-avoidance skill."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Callable

from .environment import COURSE_GOAL, COURSE_OBSTACLES, Go2ObstacleCourseEnvironment
from .course import COURSE_REFERENCE_ROUTE
from .policy import Go2ObstaclePolicy


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
    """Run the actual physics episode and return reviewable simulator metrics.

    Set ``viewer`` to open MuJoCo's native interactive window.  The free base
    and obstacle contacts remain physics-driven; visualization does not alter
    the controller or the episode result.
    """

    if side != "left":
        raise ValueError("Only side='left' has a paired MuJoCo/Webots reference route.")

    policy = Go2ObstaclePolicy(
        goal=COURSE_GOAL,
        side=side,
        reference_route=COURSE_REFERENCE_ROUTE,
        speed_scale=speed_scale,
    )
    environment = Go2ObstacleCourseEnvironment(model_dir=model_dir)
    observation = environment.reset(policy.neutral_joint_targets)
    initial_observation = observation.copy()
    policy.reset(tuple(observation["position"][:2]), COURSE_OBSTACLES)

    if playback_rate <= 0:
        raise ValueError("playback_rate must be greater than zero.")
    if viewer_hold_seconds is not None and viewer_hold_seconds < 0:
        raise ValueError("viewer_hold_seconds must be zero or greater.")

    latest_policy = {}
    control_steps = 0
    viewer_closed = False
    safe_stopped = False
    should_stop = stop_requested or (lambda: False)

    def step_episode() -> str | None:
        nonlocal observation, latest_policy, control_steps, safe_stopped
        if should_stop():
            observation = environment.safe_stop(policy.neutral_joint_targets)
            safe_stopped = True
            return "safe_stopped"
        desired, latest_policy = policy.desired_joints(observation)
        positions, velocities = environment.measured_joints()
        torque = policy.torque(
            desired,
            positions,
            velocities,
            environment.model.actuator_ctrlrange,
        )
        observation = environment.step(torque)
        control_steps += 1
        return "goal_reached" if policy.phase == "GOAL_REACHED" else None

    if viewer:
        import mujoco.viewer

        with mujoco.viewer.launch_passive(environment.model, environment.data) as native_viewer:
            native_viewer.cam.lookat[:] = (1.5, 0.0, 0.3)
            native_viewer.cam.distance = 4.8
            native_viewer.cam.azimuth = 145
            native_viewer.cam.elevation = -23
            wall_start = time.perf_counter()
            while observation["sim_time"] < max_duration_seconds and native_viewer.is_running():
                termination = step_episode()
                if termination:
                    native_viewer.sync()
                    if termination == "safe_stopped":
                        break
                    # Interactive calls hold the terminal state until the user
                    # closes the viewer. A paid visual demo supplies a bounded
                    # hold so its correlated result can still settle.
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
                    # ``launch_passive`` owns a separate viewer thread on
                    # Windows.  Leaving its context alone can leave that
                    # window visible until the operator closes it manually,
                    # which delays the terminal result and settlement proof.
                    # Close it explicitly after the documented final-state
                    # hold, then return the correlated result.
                    if native_viewer.is_running():
                        native_viewer.close()
                    break
                native_viewer.sync()
                # Make the default view watchable. The command can be sped up
                # with --playback-rate without changing the simulated physics.
                target_wall_time = observation["sim_time"] / playback_rate
                remaining = target_wall_time - (time.perf_counter() - wall_start)
                if remaining > 0:
                    time.sleep(remaining)
            viewer_closed = not native_viewer.is_running()
    else:
        while observation["sim_time"] < max_duration_seconds:
            if step_episode():
                break

    goal_distance = latest_policy.get(
        "goal_distance",
        math.hypot(
            COURSE_GOAL[0] - float(observation["position"][0]),
            COURSE_GOAL[1] - float(observation["position"][1]),
        ),
    )
    reached_goal = policy.phase == "GOAL_REACHED"
    no_collision = environment.collision_count == 0
    success = reached_goal and no_collision and not safe_stopped
    return {
        "simulator_engine": "MuJoCo",
        "robot_model": "Unitree Go2 (official unitree_mujoco MJCF)",
        "model_source_commit": "ae6a8403e272733e9996ef59990880330496177f",
        "task": "navigate_obstacles",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": (
            "safe_stopped"
            if safe_stopped
            else "goal_reached"
            if reached_goal
            else "viewer_closed"
            if viewer_closed
            else "time_limit"
        ),
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
                % (2.0 * math.pi)
                - math.pi
            ),
            3,
        ),
        "final_position": {
            "x": round(float(observation["position"][0]), 3),
            "y": round(float(observation["position"][1]), 3),
            "z": round(float(observation["position"][2]), 3),
        },
        "final_goal_distance_m": round(float(goal_distance), 3),
        "path_length_m": round(float(environment.path_length), 3),
        "minimum_clearance_m": round(float(environment.min_clearance), 3),
        "obstacle_contact_count": environment.collision_count,
        "waypoints_completed": policy.waypoints_completed,
        "waypoint_count": policy.waypoint_count,
        "controller": "shared_online_footspace_trot_with_pose_feedback",
        "policy_id": latest_policy.get("policy_id"),
        "actuator_adapter": "official_go2_pd_torque_actuators",
        "policy_state": latest_policy,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Go2's MuJoCo obstacle course.")
    parser.add_argument("--model-dir", help="Directory containing go2.xml and assets/.")
    parser.add_argument("--max-duration", type=float, default=48.0)
    parser.add_argument("--side", choices=("left",), default="left")
    parser.add_argument("--speed-scale", type=float, default=1.0)
    parser.add_argument("--viewer", action="store_true", help="Open MuJoCo's native interactive viewer.")
    parser.add_argument(
        "--playback-rate",
        type=float,
        default=1.0,
        help="Viewer speed multiplier; 1.0 is real-time and does not affect physics.",
    )
    parser.add_argument(
        "--viewer-hold-seconds",
        type=float,
        help="Seconds to hold the terminal viewer state; omit to hold until the window closes.",
    )
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = run_obstacle_nav(
        args.model_dir,
        args.max_duration,
        args.side,
        viewer=args.viewer,
        playback_rate=args.playback_rate,
        viewer_hold_seconds=args.viewer_hold_seconds,
        speed_scale=args.speed_scale,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)
