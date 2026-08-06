"""Webots implementation of the same closed-loop Spot obstacle policy.

It reads GPS and inertial-unit state every step, commands only the 12 motor
actuators, and writes an explicit result.  A lack of physical progress remains
a failure; this controller does not reposition the robot through Supervisor.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import sys

from controller import Supervisor

# This controller is started by Webots, not by the repository's Python
# launcher.  Add the package root explicitly so it can execute the exact same
# dependency-free policy core that MuJoCo uses.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))
from spot_mujoco_bridge.control_core import POLICY_ID, SpotObstacleControlCore
from spot_mujoco_bridge.course import COURSE_GOAL, COURSE_OBSTACLES, COURSE_REFERENCE_ROUTE


TIME_STEP = 16
MAX_DURATION_SECONDS = float(os.environ.get("SPOT_WEBOTS_MAX_DURATION", "55"))
# The Cyberbotics PROTO's hip axes create considerably less yaw from a pure
# stroke-amplitude differential than the Menagerie MJCF.  This fixed actuator
# calibration converts the *shared* steering scalar into a phase differential;
# it does not change planner, route, gait frequency, or feedback inputs.
WEBOTS_TURN_PHASE_CALIBRATION = 0.50
MOTOR_NAMES = (
    "front left shoulder abduction motor", "front left shoulder rotation motor", "front left elbow motor",
    "front right shoulder abduction motor", "front right shoulder rotation motor", "front right elbow motor",
    "rear left shoulder abduction motor", "rear left shoulder rotation motor", "rear left elbow motor",
    "rear right shoulder abduction motor", "rear right shoulder rotation motor", "rear right elbow motor",
)


def minimum_clearance(x: float, y: float) -> float:
    obstacle = COURSE_OBSTACLES[0]
    dx = max(abs(x - obstacle["x"]) - obstacle["half_x"], 0.0)
    dy = max(abs(y - obstacle["y"]) - obstacle["half_y"], 0.0)
    return math.hypot(dx, dy)


def main() -> None:
    robot = Supervisor()
    gps = robot.getDevice("gps")
    imu = robot.getDevice("inertial unit")
    gps.enable(TIME_STEP)
    imu.enable(TIME_STEP)
    motors = [robot.getDevice(name) for name in MOTOR_NAMES]
    for motor in motors:
        motor.setVelocity(8.0)

    policy = SpotObstacleControlCore(
        goal=COURSE_GOAL,
        side="left",
        reference_route=COURSE_REFERENCE_ROUTE,
    )
    self_node = robot.getSelf()
    self_node.enableContactPointsTracking(TIME_STEP, True)
    path_length = 0.0
    previous = None
    min_clearance = float("inf")
    max_contacts = 0
    status = "failure"
    reason = "time_limit"
    initial_position = None
    initial_yaw = None
    samples = []
    next_sample_time = 0.0
    policy_state = {}

    while robot.step(TIME_STEP) != -1:
        t = robot.getTime()
        x, y, z = gps.getValues()
        yaw = imu.getRollPitchYaw()[2]
        if initial_position is None:
            initial_position = (x, y, z)
            initial_yaw = yaw
            policy.reset((x, y), COURSE_OBSTACLES)
        if t >= next_sample_time:
            samples.append({"t": round(t, 2), "x": round(x, 3), "y": round(y, 3), "z": round(z, 3), "yaw": round(yaw, 3)})
            next_sample_time += 2.0
        if previous is not None:
            path_length += math.hypot(x - previous[0], y - previous[1])
        previous = (x, y)
        min_clearance = min(min_clearance, minimum_clearance(x, y))
        contacts = len(self_node.getContactPoints(True))
        # Feet touch the floor in normal gait, so contact points alone are not
        # a collision claim. Root intrusion in the shared obstacle rectangle
        # is a deterministic course failure in either engine.
        max_contacts = max(max_contacts, contacts)
        obstacle = COURSE_OBSTACLES[0]
        if abs(x - obstacle["x"]) < obstacle["half_x"] and abs(y - obstacle["y"]) < obstacle["half_y"]:
            reason = "obstacle_safety_envelope_entered"
            break

        plan = policy.compute_plan({"position": (x, y, z), "yaw": yaw, "sim_time": t})
        policy_state = policy.diagnostics(plan)
        if plan.phase == "GOAL_REACHED":
            status = "success"
            reason = "goal_reached"
            break

        for leg in range(4):
            base = leg * 3
            # The Webots PROTO has a different joint-zero convention than the
            # Menagerie MJCF. These abduction offsets are static calibration;
            # rotation and elbow signals below are the unmodified shared plan.
            side_sign = 1.0 if leg in (0, 2) else -1.0
            motors[base].setPosition(
                -0.10 if side_sign > 0 else 0.10
            )
            if t < policy.settle_seconds:
                hip_offset = 0.0
                elbow_offset = 0.0
            else:
                phase_offset = 0.0 if leg in (0, 3) else math.pi
                gait_sine = math.sin(
                    2.0 * math.pi * policy.GAIT_FREQUENCY_HZ * t
                    + phase_offset
                    + WEBOTS_TURN_PHASE_CALIBRATION * plan.steering * side_sign
                )
                hip_offset = (
                    policy.HIP_STROKE_RAD * (1.0 + plan.steering * side_sign) * gait_sine
                    + policy.HIP_SWING_BIAS_RAD * max(0.0, gait_sine)
                )
                elbow_offset = policy.KNEE_LIFT_RAD * max(0.0, gait_sine)
            motors[base + 1].setPosition(hip_offset)
            motors[base + 2].setPosition(elbow_offset)

        if t >= MAX_DURATION_SECONDS:
            break

    final = gps.getValues()
    final_goal_distance = math.hypot(COURSE_GOAL[0] - final[0], COURSE_GOAL[1] - final[1])
    result = {
        "simulator_engine": "Webots R2025a",
        "robot_model": "Boston Dynamics Spot (Cyberbotics PROTO)",
        "task": "navigate_obstacle_course",
        "status": status,
        "success": status == "success",
        "completion_reason": reason,
        "sim_duration_seconds": round(robot.getTime(), 3),
        "initial_position": {"x": round(initial_position[0], 3), "y": round(initial_position[1], 3), "z": round(initial_position[2], 3)},
        "initial_yaw_rad": round(initial_yaw, 3),
        "final_yaw_rad": round(imu.getRollPitchYaw()[2], 3),
        "final_position": {"x": round(final[0], 3), "y": round(final[1], 3), "z": round(final[2], 3)},
        "goal": {"x": COURSE_GOAL[0], "y": COURSE_GOAL[1]},
        "final_goal_distance_m": round(final_goal_distance, 3),
        "path_length_m": round(path_length, 3),
        "minimum_clearance_m": round(min_clearance, 3),
        "max_spot_contact_points": max_contacts,
        "waypoints_completed": policy.waypoints_completed,
        "waypoint_count": policy.waypoint_count,
        "controller": "shared_closed_loop_diagonal_gait_with_gps_imu_feedback",
        "policy_id": POLICY_ID,
        "actuator_adapter": "cyberbotics_proto_phase_calibrated_position_actuators",
        "policy_state": policy_state,
        "pose_samples": samples,
    }
    output = Path(__file__).resolve().parents[2] / "scenes" / "webots_obstacle_course_result.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    if os.environ.get("SPOT_WEBOTS_HOLD_VIEWER") == "1":
        robot.simulationSetMode(Supervisor.SIMULATION_MODE_PAUSE)
        while robot.step(TIME_STEP) != -1:
            pass
    else:
        robot.simulationQuit(0 if status == "success" else 1)


if __name__ == "__main__":
    main()
