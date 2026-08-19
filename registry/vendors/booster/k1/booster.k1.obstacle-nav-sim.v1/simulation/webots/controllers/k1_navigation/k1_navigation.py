"""Webots controller for the Booster K1 obstacle-avoidance navigation
skill. Imports the same DWA planner as the MuJoCo runner, so
sim-to-sim validation compares two physics engines running identical
policy code.

Reads GOAL_X / GOAL_Y / MAX_TIME_SEC from the environment so the same
scenario used in MuJoCo can be replayed here without editing code."""
import json
import os
import sys

import numpy as np
from controller import Supervisor

# Path to simulation/common_policy relative to this controller file
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
COMMON_POLICY_DIR = os.path.normpath(os.path.join(THIS_DIR, "..", "..", "..", "common_policy"))
sys.path.insert(0, COMMON_POLICY_DIR)
from dwa_planner import DWAConfig, RobotState, plan_step  # noqa: E402

RESULTS_PATH = os.path.normpath(os.path.join(THIS_DIR, "..", "..", "results", "metrics.json"))

GOAL_X = float(os.environ.get("GOAL_X", "5.0"))
GOAL_Y = float(os.environ.get("GOAL_Y", "0.0"))
MAX_TIME_SEC = float(os.environ.get("MAX_TIME_SEC", "60.0"))

robot = Supervisor()
timestep = int(robot.getBasicTimeStep())
dt = timestep / 1000.0

# We're the robot's own controller; get our own node for pose/velocity
self_node = robot.getSelf()
obstacle_1 = robot.getFromDef("OBSTACLE_1")
obstacle_2 = robot.getFromDef("OBSTACLE_2")

# Fallback: find obstacles by name via root children if DEF not set
if obstacle_1 is None or obstacle_2 is None:
    root = robot.getRoot().getField("children")
    for i in range(root.getCount()):
        node = root.getMFNode(i)
        name_field = node.getField("name")
        if name_field is None:
            continue
        name = name_field.getSFString()
        if name == "obstacle_1":
            obstacle_1 = node
        elif name == "obstacle_2":
            obstacle_2 = node

cfg = DWAConfig()
goal = (GOAL_X, GOAL_Y)

path_length = 0.0
prev_xy = None
collision_count = 0
step_count = 0
policy_calls = 0
t = 0.0
status = "timeout"
log = []
last_v, last_w = 0.0, 0.0

translation_field = self_node.getField("translation")
rotation_field = self_node.getField("rotation")


def get_pose():
    pos = translation_field.getSFVec3f()
    rot = rotation_field.getSFRotation()  # axis-angle (x,y,z,angle)
    # our proxy only rotates about Z, so yaw == angle * sign(axis_z)
    yaw = rot[3] if rot[2] >= 0 else -rot[3]
    return pos[0], pos[1], yaw


def get_obstacle_positions():
    obstacles = []
    if obstacle_1 is not None:
        p = obstacle_1.getField("translation").getSFVec3f()
        obstacles.append((p[0], p[1], 0.4))
    if obstacle_2 is not None:
        p = obstacle_2.getField("translation").getSFVec3f()
        obstacles.append((p[0], p[1], 0.42))
    return obstacles


def apply_velocity(v, w, yaw):
    """Kinematic integration -- the K1 proxy has no wheeled physics of
    its own, so this drives the base the same way MuJoCo's velocity
    actuators do. Documented in docs/validation-report.md."""
    x, y, _ = translation_field.getSFVec3f()
    new_yaw = yaw + w * dt
    new_x = x + v * np.cos(new_yaw) * dt
    new_y = y + v * np.sin(new_yaw) * dt
    translation_field.setSFVec3f([new_x, new_y, 0.4])
    rotation_field.setSFRotation([0, 0, 1, new_yaw])


obstacles_static = get_obstacle_positions()

while robot.step(timestep) != -1 and t < MAX_TIME_SEC:
    x, y, yaw = get_pose()
    state = RobotState(x=x, y=y, yaw=yaw, v=last_v, omega=last_w)

    if step_count % 5 == 0:  # policy at ~10-20Hz depending on basicTimeStep
        v, w, _traj = plan_step(state, goal, obstacles_static, cfg)
        policy_calls += 1
        last_v, last_w = v, w
    else:
        v, w = last_v, last_w

    apply_velocity(v, w, yaw)

    step_count += 1
    t += dt

    cur_xy = np.array([x, y])
    if prev_xy is not None:
        path_length += float(np.linalg.norm(cur_xy - prev_xy))
    prev_xy = cur_xy

    for (ox, oy, orad) in obstacles_static:
        dist = np.hypot(x - ox, y - oy)
        if dist < (orad + 0.25):  # robot radius proxy
            collision_count += 1

    dist_to_goal = float(np.hypot(goal[0] - x, goal[1] - y))

    if step_count % 20 == 0:
        log.append({"t": round(t, 2), "x": round(x, 3), "y": round(y, 3),
                     "dist_to_goal": round(dist_to_goal, 3)})

    if dist_to_goal <= 0.3 and collision_count == 0:
        status = "success"
        break
    if collision_count > 0:
        status = "collision_detected"
        break

x, y, yaw = get_pose()
final_dist = float(np.hypot(goal[0] - x, goal[1] - y))

metrics = {
    "simulator": "webots",
    "webots_version": "R2025a",
    "skillId": "k1_navigate_avoid_obstacles",
    "goal": {"x": GOAL_X, "y": GOAL_Y},
    "status": status,
    "final_pose": {"x": x, "y": y, "yaw": yaw},
    "distance_to_goal_m": round(final_dist, 4),
    "path_length_m": round(path_length, 4),
    "collision_count": collision_count,
    "sim_time_sec": round(t, 3),
    "physics_steps": step_count,
    "policy_calls": policy_calls,
    "trajectory_sample": log,
}

os.makedirs(os.path.dirname(RESULTS_PATH), exist_ok=True)
with open(RESULTS_PATH, "w") as f:
    json.dump(metrics, f, indent=2)

print(json.dumps(metrics, indent=2))
robot.simulationQuit(0)
