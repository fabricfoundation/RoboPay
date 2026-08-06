"""Real Webots controller for the converted M20 dynamic-obstacle course."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from controller import Supervisor


LEG_JOINTS = tuple(
    f"{leg}_{joint}_joint"
    for leg in ("fl", "fr", "hl", "hr")
    for joint in ("hipx", "hipy", "knee")
)
WHEEL_JOINTS = tuple(f"{leg}_wheel_joint" for leg in ("fl", "fr", "hl", "hr"))
OBSTACLE_X_M = 1.20
OBSTACLE_Y_M = 0.0
OBSTACLE_CLEAR_Y_M = 1.0
OBSTACLE_HALF_LENGTH_M = 0.10
OBSTACLE_HALF_WIDTH_M = 0.12
OBSTACLE_YIELD_RANGE_M = 0.75
OBSTACLE_YIELD_SECONDS = 2.0
OBSTACLE_CLEAR_MOVE_SECONDS = 1.2
MIN_SAFE_BASE_HEIGHT_M = 0.45
MAX_SAFE_TILT_RAD = 0.35
GOAL_COMPLETION_MARGIN_M = 0.03


def config() -> tuple[dict, Path]:
    config_path = Path(os.environ["M20_WEBOTS_CONFIG_PATH"])
    result_path = Path(os.environ["M20_WEBOTS_RESULT_PATH"])
    return json.loads(config_path.read_text(encoding="utf-8")), result_path


def _course_forward_clearance(position, yaw: float, obstacle_position) -> float | None:
    """Measure profile-course clearance, matching Spot's pose-feedback policy.

    This is not a LiDAR/camera emulation.  The controller uses Webots' live
    base pose and the physical obstacle actor; collision is independently
    checked against the same actor.
    """
    dx = float(obstacle_position[0] - position[0])
    dy = float(obstacle_position[1] - position[1])
    forward = math.cos(yaw) * dx + math.sin(yaw) * dy
    lateral = -math.sin(yaw) * dx + math.cos(yaw) * dy
    if forward <= -OBSTACLE_HALF_LENGTH_M or abs(lateral) > OBSTACLE_HALF_WIDTH_M + 0.18:
        return None
    return max(0.0, forward - OBSTACLE_HALF_LENGTH_M)


def _overlaps_obstacle(position, obstacle_position) -> bool:
    """Conservative measured base-footprint collision observation."""

    return (
        abs(float(position[0] - obstacle_position[0])) <= OBSTACLE_HALF_LENGTH_M + 0.38
        and abs(float(position[1] - obstacle_position[1])) <= OBSTACLE_HALF_WIDTH_M + 0.40
    )


def main() -> int:
    parameters, result_path = config()
    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())
    self_node = robot.getSelf()
    obstacle = robot.getFromDef("COURSE_OBSTACLE")
    if obstacle is None:
        raise RuntimeError("Webots scene is missing the physical COURSE_OBSTACLE")
    obstacle_translation = obstacle.getField("translation")
    legs = [robot.getDevice(name) for name in LEG_JOINTS]
    wheels = [robot.getDevice(name) for name in WHEEL_JOINTS]
    if any(device is None for device in [*legs, *wheels]):
        raise RuntimeError("Converted vendor M20 did not expose all 16 official motors")
    for motor in legs:
        motor.setPosition(0.0)
    for motor in wheels:
        motor.setPosition(float("inf"))
        motor.setVelocity(0.0)

    settle_ms = 1000
    max_duration_ms = int(float(parameters["max_duration_sec"]) * 1000)
    goal_distance = float(parameters["goal_distance_m"])
    wheel_speed = float(parameters["wheel_speed_rad_s"])
    elapsed_ms = 0
    phase = "settling"
    phase_history = [phase]
    start_position = None
    yield_started_ms = None
    resumed_ms = None
    min_height = float("inf")
    max_tilt = 0.0
    min_clearance = float("inf")
    obstacle_detected = False
    obstacle_released = False
    collision_detected = False
    target_reached_at = None
    completion_reason = "timeout"

    def change_phase(next_phase: str) -> None:
        nonlocal phase
        if phase != next_phase:
            phase = next_phase
            phase_history.append(phase)

    while elapsed_ms < max_duration_ms and robot.step(timestep) != -1:
        position = self_node.getPosition()
        orientation = self_node.getOrientation()
        yaw = math.atan2(float(orientation[3]), float(orientation[0]))
        height = float(position[2])
        tilt = math.acos(max(-1.0, min(1.0, float(orientation[8]))))
        min_height = min(min_height, height)
        max_tilt = max(max_tilt, tilt)
        obstacle_position = obstacle_translation.getSFVec3f()
        clearance_m = _course_forward_clearance(position, yaw, obstacle_position)
        if clearance_m is not None:
            obstacle_detected = True
            min_clearance = min(min_clearance, clearance_m)
        collision_detected = collision_detected or _overlaps_obstacle(position, obstacle_position)
        if elapsed_ms >= settle_ms and (height < MIN_SAFE_BASE_HEIGHT_M or tilt > MAX_SAFE_TILT_RAD):
            completion_reason = "unsafe_simulator_state"
            break
        if collision_detected:
            completion_reason = "course_obstacle_collision"
            break

        if elapsed_ms >= settle_ms:
            if start_position is None:
                start_position = list(position)
                change_phase("approach")
            if (
                phase == "approach"
                and clearance_m is not None
                and clearance_m <= OBSTACLE_YIELD_RANGE_M
            ):
                yield_started_ms = elapsed_ms
                change_phase("yield")
            if phase == "yield":
                elapsed_yield = (elapsed_ms - yield_started_ms) / 1000.0
                progress = max(0.0, min(1.0, (elapsed_yield - OBSTACLE_YIELD_SECONDS) / OBSTACLE_CLEAR_MOVE_SECONDS))
                obstacle_translation.setSFVec3f([OBSTACLE_X_M, OBSTACLE_Y_M + progress * OBSTACLE_CLEAR_Y_M, 0.28])
                obstacle_released = progress >= 1.0
                if obstacle_released:
                    resumed_ms = elapsed_ms
                    change_phase("resume")
            if phase in {"approach", "resume"}:
                for motor in wheels:
                    motor.setVelocity(-wheel_speed)
            else:
                for motor in wheels:
                    motor.setVelocity(0.0)
            if (
                phase == "resume"
                and float(position[0] - start_position[0])
                >= goal_distance + GOAL_COMPLETION_MARGIN_M
            ):
                target_reached_at = elapsed_ms
                change_phase("terminal_settle")
            if phase == "terminal_settle":
                for motor in wheels:
                    motor.setVelocity(0.0)
                if elapsed_ms - target_reached_at >= 400:
                    completion_reason = "dynamic_obstacle_course_complete"
                    break
        elapsed_ms += timestep

    for motor in wheels:
        motor.setVelocity(0.0)
    final_position = self_node.getPosition()
    distance = 0.0 if start_position is None else float(final_position[0] - start_position[0])
    success = (
        completion_reason == "dynamic_obstacle_course_complete"
        and distance >= goal_distance
        and obstacle_detected
        and yield_started_ms is not None
        and obstacle_released
        and not collision_detected
    )
    yield_duration = None if resumed_ms is None or yield_started_ms is None else (resumed_ms - yield_started_ms) / 1000.0
    result = {
        "simulator_engine": "Webots",
        "robot_model": "DeepRobotics M20 official URDF converted to Webots R2025a with profile-owned physical moving obstacle course (Lynx M20 Pro kinematic base)",
        "task": "navigate_obstacle_course",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": completion_reason,
        "requested_goal_distance_m": goal_distance,
        "requested_wheel_speed_rad_s": wheel_speed,
        "requested_max_duration_sec": float(parameters["max_duration_sec"]),
        "measured_forward_distance_m": round(distance, 5),
        "initial_course_position_m": start_position,
        "final_base_position_m": [round(float(value), 5) for value in final_position],
        "min_base_height_m": round(min_height, 5),
        "max_tilt_rad": round(max_tilt, 5),
        "state_authority": "Webots Supervisor base pose plus physical course obstacle state",
        "controller": "pose_feedback_course_clearance_yield_then_resume_wheel_velocity_with_leg_position_stance",
        "official_motor_count": len(legs) + len(wheels),
        "course": {
            "physical_obstacle": "COURSE_OBSTACLE",
            "perception": "pose-feedback clearance from Supervisor base pose to profile-owned physical obstacle",
            "obstacle_detected": obstacle_detected,
            "minimum_obstacle_clearance_m": None
            if not math.isfinite(min_clearance)
            else round(min_clearance, 5),
            "yield_duration_seconds": yield_duration,
            "obstacle_released": obstacle_released,
            "collision_detected": collision_detected,
            "phase_history": phase_history,
        },
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # A visual operator may inspect the final physical state without changing
    # the headless/CI course semantics. The environment variable is set only
    # for an interactive Webots launch.
    viewer_hold_seconds = max(0.0, float(os.environ.get("M20_WEBOTS_VIEWER_HOLD_SECONDS", "0")))
    hold_until = robot.getTime() + viewer_hold_seconds
    while robot.getTime() < hold_until and robot.step(timestep) != -1:
        for motor in wheels:
            motor.setVelocity(0.0)
    robot.simulationQuit(0 if success else 1)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
