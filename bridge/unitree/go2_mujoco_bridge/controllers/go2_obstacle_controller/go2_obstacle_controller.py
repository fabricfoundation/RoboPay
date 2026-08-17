"""Webots actuator adapter for the shared Go2 task controller."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys

from controller import Supervisor


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))
from go2_mujoco_bridge.control_core import POLICY_ID, Go2ObstacleControlCore
from go2_mujoco_bridge.course import COURSE_GOAL, COURSE_OBSTACLES, COURSE_REFERENCE_ROUTE
from go2_mujoco_bridge.policy import foot_inverse_kinematics


TIME_STEP = 16
MAX_DURATION_SECONDS = float(os.environ.get("GO2_WEBOTS_MAX_DURATION", "45"))
# The URDF position-motor plant produces much less yaw per left/right stride
# differential than the MJCF torque plant. This adapter-only gain calibrates
# that physical response while preserving the shared planner steering scalar.
WEBOTS_STEER_CALIBRATION = 8.0
MOTOR_NAMES = (
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
    "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
    "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
)


def yaw_from_orientation(matrix: list[float]) -> float:
    return math.atan2(matrix[3], matrix[0])


def minimum_clearance(x: float, y: float) -> float:
    return min(
        math.hypot(
            max(abs(x - obstacle["x"]) - obstacle["half_x"], 0.0),
            max(abs(y - obstacle["y"]) - obstacle["half_y"], 0.0),
        )
        for obstacle in COURSE_OBSTACLES
    )


def main() -> None:
    robot = Supervisor()
    self_node = robot.getSelf()
    motors = [robot.getDevice(name) for name in MOTOR_NAMES]
    neutral_thigh, neutral_calf = foot_inverse_kinematics(0.0, Go2ObstacleControlCore.STANCE_HEIGHT_M)
    neutral = [0.0, neutral_thigh, neutral_calf] * 4
    for motor, position in zip(motors, neutral, strict=True):
        motor.setVelocity(10.0)
        motor.setPosition(position)

    obstacle_nodes = [robot.getFromDef("RIGHT_BLOCK"), robot.getFromDef("LEFT_BLOCK")]
    for node in obstacle_nodes:
        node.enableContactPointsTracking(TIME_STEP, True)

    policy = Go2ObstacleControlCore(COURSE_GOAL, "left", COURSE_REFERENCE_ROUTE)
    initial_position = None
    initial_yaw = None
    previous = None
    path_length = 0.0
    min_clearance = float("inf")
    obstacle_contacts = 0
    samples = []
    next_sample = 0.0
    reason = "time_limit"
    policy_state = {}

    while robot.step(TIME_STEP) != -1:
        t = robot.getTime()
        x, y, z = self_node.getPosition()
        yaw = yaw_from_orientation(self_node.getOrientation())
        if initial_position is None:
            initial_position = (x, y, z)
            initial_yaw = yaw
            policy.reset((x, y), COURSE_OBSTACLES)
        if previous is not None:
            path_length += math.hypot(x - previous[0], y - previous[1])
        previous = (x, y)
        min_clearance = min(min_clearance, minimum_clearance(x, y))
        new_contacts = sum(len(node.getContactPoints(True)) for node in obstacle_nodes)
        obstacle_contacts += new_contacts
        if t >= next_sample:
            samples.append({"t": round(t, 2), "x": round(x, 3), "y": round(y, 3), "z": round(z, 3), "yaw": round(yaw, 3)})
            next_sample += 2.0

        plan = policy.compute_plan({"position": (x, y, z), "yaw": yaw, "sim_time": t})
        policy_state = policy.diagnostics(plan)
        if new_contacts:
            reason = "physical_obstacle_contact"
            break
        if z < 0.12 or z > 0.55:
            reason = "unstable_body_height"
            break
        if plan.phase == "GOAL_REACHED":
            reason = "goal_reached"
            break

        desired = neutral.copy()
        for leg, (foot_x, foot_z) in enumerate(zip(plan.foot_x_m, plan.foot_z_m, strict=True)):
            side_sign = 1.0 if leg in (1, 3) else -1.0
            source_factor = 1.0 + plan.steering * side_sign
            nominal_x = foot_x / source_factor
            calibrated_factor = max(
                0.35,
                min(1.65, 1.0 + WEBOTS_STEER_CALIBRATION * plan.steering * side_sign),
            )
            calibrated_x = nominal_x * calibrated_factor
            desired[3 * leg + 1], desired[3 * leg + 2] = foot_inverse_kinematics(calibrated_x, foot_z)
        for motor, position in zip(motors, desired, strict=True):
            motor.setPosition(position)
        if t >= MAX_DURATION_SECONDS:
            break

    final = self_node.getPosition()
    goal_distance = math.hypot(COURSE_GOAL[0] - final[0], COURSE_GOAL[1] - final[1])
    success = reason == "goal_reached" and obstacle_contacts == 0
    result = {
        "simulator_engine": "Webots R2025a",
        "robot_model": "Unitree Go2 (official unitree_ros URDF)",
        "model_source_commit": "f3772ce54c56ef2d34c6aee8100bc768896c7d19",
        "task": "navigate_obstacles",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": reason,
        "sim_duration_seconds": round(robot.getTime(), 3),
        "initial_position": dict(zip(("x", "y", "z"), map(lambda v: round(v, 3), initial_position))),
        "initial_yaw_rad": round(initial_yaw, 3),
        "final_position": dict(zip(("x", "y", "z"), map(lambda v: round(v, 3), final))),
        "final_yaw_rad": round(yaw_from_orientation(self_node.getOrientation()), 3),
        "heading_change_rad": round(
            abs(
                (yaw_from_orientation(self_node.getOrientation()) - initial_yaw + math.pi)
                % (2.0 * math.pi)
                - math.pi
            ),
            3,
        ),
        "goal": {"x": COURSE_GOAL[0], "y": COURSE_GOAL[1]},
        "final_goal_distance_m": round(goal_distance, 3),
        "path_length_m": round(path_length, 3),
        "minimum_clearance_m": round(min_clearance, 3),
        "obstacle_contact_count": obstacle_contacts,
        "waypoints_completed": policy.waypoints_completed,
        "waypoint_count": policy.waypoint_count,
        "controller": "shared_online_footspace_trot_with_measured_pose_feedback",
        "policy_id": POLICY_ID,
        "actuator_adapter": "official_urdf_position_motors",
        "adapter_steer_calibration": WEBOTS_STEER_CALIBRATION,
        "policy_state": policy_state,
        "pose_samples": samples,
    }
    output = PACKAGE_ROOT / "scenes" / "webots_obstacle_nav_result.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    if os.environ.get("GO2_WEBOTS_HOLD_VIEWER") == "1":
        robot.simulationSetMode(Supervisor.SIMULATION_MODE_PAUSE)
        while robot.step(TIME_STEP) != -1:
            pass
    else:
        robot.simulationQuit(0 if success else 1)


if __name__ == "__main__":
    main()
