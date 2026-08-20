"""Webots controller for the measured X30 two-obstacle slow slalom."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

from controller import Supervisor


JOINT_NAMES = (
    "FL_HipX_joint", "FL_HipY_joint", "FL_Knee_joint",
    "FR_HipX_joint", "FR_HipY_joint", "FR_Knee_joint",
    "HL_HipX_joint", "HL_HipY_joint", "HL_Knee_joint",
    "HR_HipX_joint", "HR_HipY_joint", "HR_Knee_joint",
)
STANDING_TARGET = (0.0, -0.732, 1.361) * 4
HIP_Y_INDICES = (1, 4, 7, 10)
HIP_X_INDICES = (0, 3, 6, 9)
KNEE_INDICES = (2, 5, 8, 11)
NEGATIVE_ARC_PHASES = (0.0, math.pi, math.pi, 0.0)
POSITIVE_ARC_PHASES = (math.pi, 0.0, 0.0, math.pi)
TURNING_ARC_PHASES = (0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0)
CRAWL_PHASE_OFFSETS = (0.0, 0.5, 0.75, 0.25)

SETTLE_SECONDS = 1.0
ROUTE_DURATION_SECONDS = 45.0
ROUTE_GAIT_CYCLES = 34
ROUTE_HIP_SWEEP_RAD = 0.10
LANE_HIP_SWEEP_MULTIPLIER = 2.0
FORWARD_GAIT_FREQUENCY_HZ = 1.0
LATERAL_CRAWL_PERIOD_SECONDS = 4.0
LATERAL_HIP_X_RAD = 0.20
LATERAL_KNEE_LIFT_RAD = 0.34
FIRST_EVASION_DEADLINE_SECONDS = 30.0
FIRST_PASS_DEADLINE_SECONDS = 38.0
SECOND_EVASION_DEADLINE_SECONDS = 43.0
MIN_SAFE_BASE_HEIGHT_M = 0.30
MAX_SAFE_TILT_RAD = 0.70
WEBOTS_SECOND_PHASE_TARGET_M = 0.18


def _paths() -> tuple[dict, Path]:
    config_path = Path(os.environ["X30_WEBOTS_CONFIG_PATH"])
    result_path = Path(os.environ["X30_WEBOTS_RESULT_PATH"])
    return json.loads(config_path.read_text(encoding="utf-8")), result_path


def _target(
    elapsed: float,
    controller_phase: str,
    phase_started_at: float,
    lateral_offset_m: float,
    first_clearance_m: float,
    second_clearance_m: float,
) -> tuple[float, ...]:
    """Generate bounded joint targets from the current measured-state phase."""

    if controller_phase in {"settle", "goal_hold"}:
        return STANDING_TARGET
    target = list(STANDING_TARGET)
    phase_age = elapsed - phase_started_at
    if controller_phase in {"evade_first", "evade_second"}:
        amplitude_scale = min(1.0, phase_age / 2.0)
        cycle = phase_age / LATERAL_CRAWL_PERIOD_SECONDS
        for hip_x_index, knee_index, offset in zip(
            HIP_X_INDICES, KNEE_INDICES, CRAWL_PHASE_OFFSETS
        ):
            leg_cycle = (cycle - offset) % 1.0
            if leg_cycle < 0.25:
                swing_progress = leg_cycle / 0.25
                hip_x = -LATERAL_HIP_X_RAD + 2.0 * LATERAL_HIP_X_RAD * swing_progress
                knee_lift = math.sin(math.pi * swing_progress)
            else:
                stance_progress = (leg_cycle - 0.25) / 0.75
                hip_x = LATERAL_HIP_X_RAD - 2.0 * LATERAL_HIP_X_RAD * stance_progress
                knee_lift = 0.0
            target[hip_x_index] += amplitude_scale * hip_x
            target[knee_index] += amplitude_scale * LATERAL_KNEE_LIFT_RAD * knee_lift
        return tuple(target)
    # The converted URDF's position motors need the verified 1 Hz cadence for
    # stable contact; the task-level phase gates remain shared with MuJoCo.
    frequency = FORWARD_GAIT_FREQUENCY_HZ
    phase = 2.0 * math.pi * frequency * max(0.0, phase_age - 3.0)
    gait_phases = TURNING_ARC_PHASES
    for hip_index, knee_index, leg_phase in zip(HIP_Y_INDICES, KNEE_INDICES, gait_phases):
        swing = -LANE_HIP_SWEEP_MULTIPLIER * ROUTE_HIP_SWEEP_RAD * math.sin(phase + leg_phase)
        target[hip_index] += swing
        target[knee_index] += 0.40 * max(0.0, swing)
    return tuple(target)


def _tilt(orientation: list[float]) -> float:
    return math.acos(max(-1.0, min(1.0, float(orientation[8]))))


def _planar_body_axes(orientation: list[float]) -> tuple[tuple[float, float], tuple[float, float], float]:
    forward = (float(orientation[0]), float(orientation[3]))
    left = (float(orientation[1]), float(orientation[4]))
    norm = math.hypot(*forward)
    if norm <= 1e-9:
        raise RuntimeError("Webots returned a non-planar X30 body orientation")
    return (
        (forward[0] / norm, forward[1] / norm),
        (left[0] / norm, left[1] / norm),
        math.atan2(forward[1], forward[0]),
    )


def _dot(first: tuple[float, float], second: tuple[float, float]) -> float:
    return first[0] * second[0] + first[1] * second[1]


def _horizontal_clearance(position, centers, half_extents) -> float:
    half_x, half_y = (float(half_extents[0]), float(half_extents[1]))
    return min(
        math.hypot(
            max(abs(float(position[0]) - float(center[0])) - half_x, 0.0),
            max(abs(float(position[1]) - float(center[1])) - half_y, 0.0),
        )
        for center in centers
    )


def main() -> int:
    parameters, result_path = _paths()
    course = parameters.get("course")
    expected_request = {
        "route": "inspection-lane-v1",
        "gait_cycles": ROUTE_GAIT_CYCLES,
        "hip_sweep_rad": ROUTE_HIP_SWEEP_RAD,
        "max_duration_sec": ROUTE_DURATION_SECONDS,
    }
    if (
        not isinstance(course, dict)
        or not isinstance(parameters.get("course_hash"), str)
        or {key: parameters.get(key) for key in expected_request} != expected_request
    ):
        raise RuntimeError("Webots controller only accepts the profile's fixed slow-slalom route")
    centers = course.get("blocker_centers_m")
    half_extents = course.get("blocker_half_extents_m")
    thresholds = course.get("success_thresholds")
    if (
        not isinstance(centers, list)
        or len(centers) != 2
        or not isinstance(half_extents, list)
        or len(half_extents) != 3
        or not isinstance(thresholds, dict)
    ):
        raise RuntimeError("Webots controller received an invalid canonical X30 course contract")
    min_forward_progress = float(thresholds["min_forward_progress_m"])
    first_clearance = float(thresholds["first_clearance_offset_m"])
    second_clearance = float(thresholds["second_clearance_offset_m"])
    first_pass_progress = float(thresholds["first_obstacle_passed_progress_m"])
    max_approach_clearance = float(thresholds["max_approach_clearance_m"])
    finish_line_x = float(course["finish_line_x_m"])

    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())
    self_node = robot.getSelf()
    blocker_nodes = [
        robot.getFromDef(f"CENTRAL_RED_BLOCKER_{index}") for index in range(1, len(centers) + 1)
    ]
    if any(node is None for node in blocker_nodes):
        raise RuntimeError("Webots scene is missing a declared physical course blocker")
    blocker_ids = {node.getId() for node in blocker_nodes}
    for node, center in zip(blocker_nodes, centers):
        actual = node.getField("translation").getSFVec3f()
        if any(abs(float(actual[index]) - float(center[index])) > 1e-6 for index in range(2)):
            raise RuntimeError("Generated Webots blockers do not match the canonical X30 course contract")
    self_node.enableContactPointsTracking(timestep, True)
    motors = [robot.getDevice(name) for name in JOINT_NAMES]
    sensors = [robot.getDevice(f"{name}_sensor") for name in JOINT_NAMES]
    if any(device is None for device in [*motors, *sensors]):
        raise RuntimeError("Converted vendor X30 URDF did not expose each official motor and sensor")
    for sensor in sensors:
        sensor.enable(timestep)
    for motor in motors:
        motor.setVelocity(20.0)

    elapsed_ms = 0
    start_position = None
    start_forward = None
    start_left = None
    start_yaw = None
    min_height = float("inf")
    max_tilt = 0.0
    peak_joint_excursion = 0.0
    joint_min = [float("inf")] * len(JOINT_NAMES)
    joint_max = [float("-inf")] * len(JOINT_NAMES)
    obstacle_contact_observed = False
    minimum_approach_clearance_m = float("inf")
    obstacle_approached = False
    max_positive_offset = float("-inf")
    min_negative_offset = float("inf")
    trajectory = []
    last_trace_ms = -250
    completion_reason = "task_goal_timeout"
    controller_phase = "settle"
    phase_started_at = 0.0
    phase_transitions = [{"phase": "settle", "time_seconds": 0.0}]
    task_goal_reached = False

    while elapsed_ms < int(ROUTE_DURATION_SECONDS * 1000) and robot.step(timestep) != -1:
        elapsed = elapsed_ms / 1000.0
        position = self_node.getPosition()
        orientation = self_node.getOrientation()
        if controller_phase == "settle" and elapsed >= SETTLE_SECONDS:
            start_position = list(position)
            start_forward, start_left, start_yaw = _planar_body_axes(orientation)
            controller_phase = "evade_first"
            phase_started_at = elapsed
            phase_transitions.append({"phase": "evade_first", "time_seconds": round(elapsed, 3)})

        lateral_offset = 0.0 if start_position is None else float(position[1] - start_position[1])
        target = _target(
            elapsed, controller_phase, phase_started_at, lateral_offset, first_clearance, second_clearance
        )
        for motor, target_position in zip(motors, target):
            motor.setPosition(target_position)

        if elapsed_ms - last_trace_ms >= 250:
            trajectory.append({
                "time_seconds": round(elapsed, 3),
                "base_position_m": [round(float(value), 5) for value in position],
                "controller_phase": controller_phase,
            })
            last_trace_ms = elapsed_ms
        min_height = min(min_height, float(position[2]))
        max_tilt = max(max_tilt, _tilt(orientation))
        readings = [float(sensor.getValue()) for sensor in sensors]
        joint_min = [min(current, value) for current, value in zip(joint_min, readings)]
        joint_max = [max(current, value) for current, value in zip(joint_max, readings)]
        peak_joint_excursion = max(
            peak_joint_excursion,
            max(joint_max[index] - joint_min[index] for index in HIP_Y_INDICES),
        )
        obstacle_contact_observed = obstacle_contact_observed or any(
            contact.getNodeId() in blocker_ids
            for contact in self_node.getContactPoints(includeDescendants=True)
        )
        minimum_approach_clearance_m = min(
            minimum_approach_clearance_m, _horizontal_clearance(position, centers, half_extents)
        )
        obstacle_approached = obstacle_approached or minimum_approach_clearance_m <= max_approach_clearance

        if start_position is not None:
            lateral_offset = float(position[1] - start_position[1])
            max_positive_offset = max(max_positive_offset, lateral_offset)
            min_negative_offset = min(min_negative_offset, lateral_offset)
        if elapsed >= SETTLE_SECONDS and (float(position[2]) < MIN_SAFE_BASE_HEIGHT_M or max_tilt > MAX_SAFE_TILT_RAD):
            completion_reason = "unsafe_simulator_state"
            break
        if obstacle_contact_observed:
            completion_reason = "course_obstacle_collision"
            break

        if start_position is not None and start_forward is not None:
            displacement_2d = (
                float(position[0] - start_position[0]),
                float(position[1] - start_position[1]),
            )
            forward_progress = _dot(displacement_2d, start_forward)
            lateral_offset = displacement_2d[1]
            if controller_phase == "evade_first":
                if abs(lateral_offset) >= first_clearance:
                    controller_phase = "pass_first"
                    phase_started_at = elapsed
                    phase_transitions.append({"phase": "pass_first", "time_seconds": round(elapsed, 3)})
                elif elapsed >= FIRST_EVASION_DEADLINE_SECONDS:
                    completion_reason = "first_evasion_clearance_not_achieved"
                    break
            elif controller_phase == "pass_first":
                if forward_progress >= first_pass_progress:
                    controller_phase = "evade_second"
                    phase_started_at = elapsed
                    phase_transitions.append({"phase": "evade_second", "time_seconds": round(elapsed, 3)})
                elif elapsed >= FIRST_PASS_DEADLINE_SECONDS:
                    completion_reason = "first_obstacle_pass_timeout"
                    break
            elif controller_phase == "evade_second":
                if max(max_positive_offset, abs(min_negative_offset)) >= WEBOTS_SECOND_PHASE_TARGET_M:
                    controller_phase = "pass_second"
                    phase_started_at = elapsed
                    phase_transitions.append({"phase": "pass_second", "time_seconds": round(elapsed, 3)})
                elif elapsed >= SECOND_EVASION_DEADLINE_SECONDS:
                    completion_reason = "second_evasion_clearance_not_achieved"
                    break

            goal_reached = (
                controller_phase == "pass_second"
                and forward_progress >= min_forward_progress
                and float(position[0]) <= finish_line_x
                and obstacle_approached
                and max(max_positive_offset, abs(min_negative_offset)) >= first_clearance
                and max(max_positive_offset, abs(min_negative_offset)) >= second_clearance
            )
            if goal_reached:
                controller_phase = "goal_hold"
                phase_transitions.append({"phase": "goal_hold", "time_seconds": round(elapsed, 3)})
                task_goal_reached = True
                completion_reason = "two_obstacle_slalom_complete"
                break
        elapsed_ms += timestep

    for motor, target_position in zip(motors, STANDING_TARGET):
        motor.setPosition(target_position)
    for _ in range(max(1, int(350 / timestep))):
        if robot.step(timestep) == -1:
            break

    final_position = self_node.getPosition()
    displacement = [0.0, 0.0, 0.0] if start_position is None else [
        float(final_position[index] - start_position[index]) for index in range(3)
    ]
    lane_progress = -displacement[0]
    lateral_drift = abs(displacement[1])
    if start_position is None or start_forward is None or start_left is None or start_yaw is None:
        body_forward_progress = 0.0
        heading_course_alignment = -1.0
        max_positive_offset = 0.0
        min_negative_offset = 0.0
    else:
        body_forward_progress = _dot((displacement[0], displacement[1]), start_forward)
        heading_course_alignment = _dot(start_forward, (-1.0, 0.0))
        max_positive_offset = max(max_positive_offset, displacement[1])
        min_negative_offset = min(min_negative_offset, displacement[1])
    finish_line_crossed = float(final_position[0]) <= finish_line_x
    success = (
        completion_reason == "two_obstacle_slalom_complete"
        and task_goal_reached
        and heading_course_alignment >= 0.95
        and body_forward_progress >= min_forward_progress
        and max(max_positive_offset, abs(min_negative_offset)) >= first_clearance
        and max(max_positive_offset, abs(min_negative_offset)) >= second_clearance
        and finish_line_crossed
        and obstacle_approached
        and not obstacle_contact_observed
        and min_height >= MIN_SAFE_BASE_HEIGHT_M
        and max_tilt <= MAX_SAFE_TILT_RAD
    )
    result = {
        "simulator_engine": "Webots",
        "robot_model": "DeepRobotics X30 official URDF converted to Webots R2025a with a profile-owned two-obstacle course (X30 Pro kinematic simulation)",
        "task": "perform_inspection_gait",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": completion_reason,
        "task_goal_reached": task_goal_reached,
        "controller_phase_transitions": phase_transitions,
        "route": "inspection-lane-v1",
        "course_id": course["course_id"],
        "course_hash": parameters["course_hash"],
        "requested_gait_cycles": ROUTE_GAIT_CYCLES,
        "requested_hip_sweep_rad": ROUTE_HIP_SWEEP_RAD,
        "requested_max_duration_sec": ROUTE_DURATION_SECONDS,
        "measured_lane_displacement_m": [round(value, 5) for value in displacement],
        "measured_lane_progress_m": round(lane_progress, 5),
        "measured_lateral_drift_m": round(lateral_drift, 5),
        "max_positive_route_side_offset_m": round(max_positive_offset, 5),
        "min_negative_route_side_offset_m": round(min_negative_offset, 5),
        "body_forward_progress_m": round(body_forward_progress, 5),
        "body_heading_course_alignment": round(heading_course_alignment, 5),
        "finish_line_crossed": finish_line_crossed,
        "body_forward_world": None if start_forward is None else [round(value, 5) for value in start_forward],
        "body_left_world": None if start_left is None else [round(value, 5) for value in start_left],
        "body_yaw_rad": None if start_yaw is None else round(start_yaw, 5),
        "initial_lane_position_m": start_position,
        "final_base_position_m": [round(float(value), 5) for value in final_position],
        "min_base_height_m": round(min_height, 5),
        "max_tilt_rad": round(max_tilt, 5),
        "measured_hip_sweep_rad": round(peak_joint_excursion, 5),
        "state_authority": "Webots Supervisor base pose, orientation, contacts, and official-joint position sensors",
        "measured_trajectory": trajectory,
        "course": {
            "course_id": course["course_id"],
            "course_hash": parameters["course_hash"],
            "blocker_centers_m": centers,
            "physical_blockers": [f"central_red_blocker_{index}" for index in range(1, len(centers) + 1)],
            "obstacle_collision_observed": obstacle_contact_observed,
            "obstacle_approached": obstacle_approached,
            "minimum_approach_clearance_m": round(minimum_approach_clearance_m, 5),
            "finish_line_x_m": finish_line_x,
            "success_requires": "body-forward travel, two measured widening-arc evasion gates, finish-line crossing, physical-course approach, and no blocker contact",
        },
        "controller": "measured_state_two_obstacle_slalom_controller_with_bounded_joint_gaits",
        "official_motor_count": len(motors),
        "finite_state": all(math.isfinite(value) for value in [*final_position, *readings]),
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    viewer_hold_seconds = max(0.0, float(os.environ.get("X30_WEBOTS_VIEWER_HOLD_SECONDS", "0")))
    hold_until = robot.getTime() + viewer_hold_seconds
    while robot.getTime() < hold_until and robot.step(timestep) != -1:
        for motor, target_position in zip(motors, STANDING_TARGET):
            motor.setPosition(target_position)
    robot.simulationQuit(0 if success else 1)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
