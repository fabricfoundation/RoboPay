"""Task-level Webots adapter for the official LimX WF_TRON2A.

The profile-owned planner computes the route online from measured Webots
state.  Supervisor applies bounded chassis velocity commands to the dynamic
vendor model; it never writes translation, rotation, or a prerecorded pose.
Terminal success remains authoritative simulator state.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

from controller import Supervisor


HERE = Path(__file__).resolve()
PROFILE_ROOT = HERE.parents[4]
BRIDGE_ROOT = PROFILE_ROOT / "bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from limx_tron2_sim.contracts import MAX_DURATION_SECONDS, NAVIGATION_SKILL, STOP_SKILL
from limx_tron2_sim.course import GOAL, OBSTACLES, WAYPOINTS, RoutePlanner, obstacle_clearance
from limx_tron2_sim.policy import JOINT_NAMES


SETTLE_SECONDS = 1.5
MAX_CHASSIS_SPEED_MPS = 0.25
MAX_YAW_RATE_RAD_S = 0.45
MAX_CHASSIS_ACCEL_MPS2 = 0.30
MAX_YAW_ACCEL_RAD_S2 = 0.80
ORIENTATION_DAMPING_GAIN = 20.0
MAX_ORIENTATION_RATE_RAD_S = 5.0
HEIGHT_HOLD_GAIN = 5.0
MAX_VERTICAL_SPEED_MPS = 0.5
MIN_SAFE_BASE_HEIGHT_M = 0.42
MAX_SAFE_TILT_RAD = 0.55
WHEEL_RADIUS_M = 0.100
WHEEL_INDICES = (4, 9)


def _parameters() -> tuple[dict, Path]:
    config_path = Path(os.environ["LIMX_TRON2_WEBOTS_CONFIG_PATH"])
    result_path = Path(os.environ["LIMX_TRON2_WEBOTS_RESULT_PATH"])
    return json.loads(config_path.read_text(encoding="utf-8")), result_path


def _rpy(rotation: list[float]) -> tuple[float, float, float]:
    pitch = math.asin(max(-1.0, min(1.0, -float(rotation[6]))))
    roll = math.atan2(float(rotation[7]), float(rotation[8]))
    yaw = math.atan2(float(rotation[3]), float(rotation[0]))
    return roll, pitch, yaw


def _clip(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def main() -> int:
    parameters, result_path = _parameters()
    skill_id = parameters.get("skill_id")
    duration = float(parameters.get("max_duration_sec", 0.0))
    if skill_id not in {NAVIGATION_SKILL, STOP_SKILL}:
        raise RuntimeError("Webots controller received an unregistered skill")
    if duration <= 0.0 or duration > MAX_DURATION_SECONDS:
        raise RuntimeError("Webots controller received an unbounded duration")

    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())
    dt = timestep / 1000.0
    self_node = robot.getSelf()
    self_node.enableContactPointsTracking(timestep, True)
    motors = [robot.getDevice(name) for name in JOINT_NAMES]
    for index, motor in enumerate(motors):
        if motor is None:
            raise RuntimeError(f"official WF_TRON2A Webots model is missing {JOINT_NAMES[index]}")
        if index in WHEEL_INDICES:
            motor.setPosition(float("inf"))
            motor.setVelocity(40.0)
            motor.setTorque(0.0)
        else:
            motor.setVelocity(4.0)
            motor.setPosition(0.0)

    planner = RoutePlanner()
    elapsed = 0.0
    path_length = 0.0
    detected: set[str] = set()
    minimum_clearance = float("inf")
    collision = False
    max_tilt = 0.0
    min_height = float("inf")
    max_height = -float("inf")
    max_measured_speed = 0.0
    velocity_command_count = 0
    terminal_reason = "timeout"
    commanded_linear = 0.0
    commanded_angular = 0.0
    initial = tuple(float(value) for value in self_node.getPosition())
    support_height = initial[2]
    previous_xy: tuple[float, float] | None = None
    command_samples: list[dict] = []

    while elapsed < duration and robot.step(timestep) != -1:
        position = self_node.getPosition()
        x, y, z = (float(value) for value in position)
        roll, pitch, yaw = _rpy(self_node.getOrientation())
        velocity = [float(value) for value in self_node.getVelocity()]
        measured_speed = math.hypot(velocity[0], velocity[1])
        max_measured_speed = max(max_measured_speed, measured_speed)
        max_tilt = max(max_tilt, abs(roll), abs(pitch))
        min_height = min(min_height, z)
        max_height = max(max_height, z)
        if previous_xy is not None:
            path_length += math.hypot(x - previous_xy[0], y - previous_xy[1])
        previous_xy = (x, y)

        for obstacle in OBSTACLES:
            clearance = obstacle_clearance(x, y, obstacle)
            minimum_clearance = min(minimum_clearance, clearance)
            if math.hypot(x - obstacle.x, y - obstacle.y) <= 1.45:
                detected.add(obstacle.name)
        collision = minimum_clearance < -0.02
        if collision:
            terminal_reason = "collision"
            break
        if elapsed > SETTLE_SECONDS and (
            z < MIN_SAFE_BASE_HEIGHT_M or max(abs(roll), abs(pitch)) > MAX_SAFE_TILT_RAD
        ):
            terminal_reason = "unsafe_base_state"
            break

        if skill_id == STOP_SKILL:
            requested_linear = 0.0
            requested_angular = 0.0
        elif elapsed < SETTLE_SECONDS:
            requested_linear = 0.0
            requested_angular = 0.0
        else:
            requested_linear, _, requested_angular = planner.command(x, y, yaw)
        requested_linear = _clip(requested_linear, MAX_CHASSIS_SPEED_MPS)
        requested_angular = _clip(requested_angular, MAX_YAW_RATE_RAD_S)
        commanded_linear += _clip(
            requested_linear - commanded_linear,
            MAX_CHASSIS_ACCEL_MPS2 * dt,
        )
        commanded_angular += _clip(
            requested_angular - commanded_angular,
            MAX_YAW_ACCEL_RAD_S2 * dt,
        )
        if elapsed < SETTLE_SECONDS:
            support_height = z
        vertical_speed = _clip(
            HEIGHT_HOLD_GAIN * (support_height - z),
            MAX_VERTICAL_SPEED_MPS,
        )

        command = [
            commanded_linear * math.cos(yaw),
            commanded_linear * math.sin(yaw),
            vertical_speed,
            _clip(-ORIENTATION_DAMPING_GAIN * roll, MAX_ORIENTATION_RATE_RAD_S),
            _clip(-ORIENTATION_DAMPING_GAIN * pitch, MAX_ORIENTATION_RATE_RAD_S),
            commanded_angular,
        ]
        self_node.setVelocity(command)
        velocity_command_count += 1
        if len(command_samples) < 80 and int(elapsed * 2.0) > len(command_samples):
            command_samples.append(
                {
                    "t": round(elapsed, 3),
                    "measured_pose": {
                        "x": round(x, 4),
                        "y": round(y, 4),
                        "z": round(z, 4),
                        "roll": round(roll, 4),
                        "pitch": round(pitch, 4),
                        "yaw": round(yaw, 4),
                    },
                    "command_linear_mps": round(commanded_linear, 4),
                    "command_yaw_rad_s": round(commanded_angular, 4),
                    "measured_speed_mps": round(measured_speed, 4),
                    "waypoints_completed": len(planner.visited),
                }
            )

        elapsed += dt
        if skill_id == STOP_SKILL and elapsed >= SETTLE_SECONDS + 0.5:
            terminal_reason = "safe_stopped"
            break
        if skill_id == NAVIGATION_SKILL and planner.complete:
            if math.hypot(x - GOAL[0], y - GOAL[1]) <= 0.34:
                terminal_reason = "goal_reached"
                break

    current_velocity = [float(value) for value in self_node.getVelocity()]
    self_node.setVelocity([0.0, 0.0, current_velocity[2], 0.0, 0.0, 0.0])
    for index in WHEEL_INDICES:
        motors[index].setTorque(0.0)
    final = tuple(float(value) for value in self_node.getPosition())
    final_roll, final_pitch, final_yaw = _rpy(self_node.getOrientation())
    goal_distance = math.hypot(final[0] - GOAL[0], final[1] - GOAL[1])
    displacement = math.hypot(final[0] - initial[0], final[1] - initial[1])
    success = terminal_reason in {"goal_reached", "safe_stopped"} and not collision
    if terminal_reason == "goal_reached":
        success = (
            success
            and len(planner.visited) == len(WAYPOINTS)
            and len(detected) == len(OBSTACLES)
            and goal_distance <= 0.34
            and max_measured_speed <= MAX_CHASSIS_SPEED_MPS + 0.08
        )

    result = {
        "success": success,
        "skill": skill_id,
        "simulator": "webots",
        "model_variant": "WF_TRON2A vendor URDF converted to Webots R2025a",
        "controller": "profile-owned online route planner with bounded Supervisor chassis velocity adapter",
        "actuation_scope": "task-level chassis velocity; passive wheel joints respond to physical floor contact",
        "state_authority": "measured Webots root pose, velocity, orientation, contacts and obstacle clearance",
        "supervisor_root_pose_writes": 0,
        "supervisor_velocity_commands": velocity_command_count,
        "trajectory_replay": False,
        "terminal_reason": terminal_reason,
        "waypoints_completed": len(planner.visited),
        "waypoints_total": len(WAYPOINTS),
        "detected_obstacles": sorted(detected),
        "collision": collision,
        "minimum_clearance_m": round(minimum_clearance, 4),
        "path_length_m": round(path_length, 4),
        "physical_displacement_m": round(displacement, 4),
        "goal_distance_m": round(goal_distance, 4),
        "max_measured_speed_mps": round(max_measured_speed, 4),
        "vertical_excursion_m": round(max_height - min_height, 4),
        "max_tilt_rad": round(max_tilt, 4),
        "final_base_pose": {
            "x": round(final[0], 4),
            "y": round(final[1], 4),
            "z": round(final[2], 4),
            "roll": round(final_roll, 4),
            "pitch": round(final_pitch, 4),
            "yaw": round(final_yaw, 4),
        },
        "sim_duration_seconds": round(elapsed, 3),
        "command_samples": command_samples,
        "safe_stop_applied": terminal_reason == "safe_stopped",
    }
    if not success:
        result["error_code"] = "COURSE_NOT_COMPLETED"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    hold_seconds = max(0.0, float(os.environ.get("LIMX_TRON2_WEBOTS_VIEWER_HOLD_SECONDS", "0")))
    hold_until = robot.getTime() + hold_seconds
    while robot.getTime() < hold_until and robot.step(timestep) != -1:
        self_node.setVelocity([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    robot.simulationQuit(0 if success else 1)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
