"""MuJoCo runner for the Booster K1 obstacle-avoidance navigation
skill. Steps physics in a closed loop where the DWA policy reads
robot/obstacle state from the simulator itself, and writes metrics
to results/metrics.json.

Usage:
    python runner.py --goal_x 5.0 --goal_y 0.0 --max_time_sec 60
"""
import argparse
import json
import os
import sys

import numpy as np
import mujoco

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common_policy"))
from dwa_planner import DWAConfig, RobotState, plan_step  # noqa: E402

SCENE_PATH = os.path.join(os.path.dirname(__file__), "scenes", "booster_k1_obstacle_nav.xml")
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "results", "metrics.json")


def get_obstacles(model, data):
    """Reads obstacle positions + radii from the live sim state."""
    obstacles = []
    for name, radius in [("obstacle_1", 0.4), ("obstacle_2", 0.42)]:
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        pos = data.xpos[body_id]
        obstacles.append((float(pos[0]), float(pos[1]), radius))
    return obstacles


def get_robot_state(model, data):
    # qpos/qvel layout: [jx, jy, jz] since the base uses slide+slide+hinge
    x, y, yaw = data.qpos[0], data.qpos[1], data.qpos[2]
    vx, vy, omega = data.qvel[0], data.qvel[1], data.qvel[2]
    return RobotState(x=float(x), y=float(y), yaw=float(yaw),
                       v=float(np.hypot(vx, vy)), omega=float(omega))


def check_collision(model, data):
    for i in range(data.ncon):
        con = data.contact[i]
        g1 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, con.geom1)
        g2 = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, con.geom2)
        names = {g1, g2}
        if "k1_torso" in names and ("obs1" in names or "obs2" in names):
            return True
    return False


def run(goal_x, goal_y, max_time_sec, dt=0.01):
    model = mujoco.MjModel.from_xml_path(SCENE_PATH)
    data = mujoco.MjData(model)
    cfg = DWAConfig()
    goal = (goal_x, goal_y)

    path_length = 0.0
    prev_xy = None
    collision_count = 0
    step_count = 0
    policy_calls = 0
    t = 0.0
    status = "timeout"

    log = []

    while t < max_time_sec:
        state = get_robot_state(model, data)
        obstacles = get_obstacles(model, data)

        if step_count % 10 == 0:  # policy runs at 10Hz, physics at 100Hz
            v, w, _traj = plan_step(state, goal, obstacles, cfg)
            policy_calls += 1
            last_v, last_w = v, w
        else:
            v, w = last_v, last_w

        data.ctrl[0] = v * np.cos(state.yaw)
        data.ctrl[1] = v * np.sin(state.yaw)
        data.ctrl[2] = w

        mujoco.mj_step(model, data)
        step_count += 1
        t += dt

        cur_xy = np.array([state.x, state.y])
        if prev_xy is not None:
            path_length += float(np.linalg.norm(cur_xy - prev_xy))
        prev_xy = cur_xy

        if check_collision(model, data):
            collision_count += 1
            if collision_count == 1:
                status = "collision_detected"

        dist_to_goal = float(np.hypot(goal[0] - state.x, goal[1] - state.y))

        if step_count % 100 == 0:
            log.append({"t": round(t, 2), "x": round(state.x, 3), "y": round(state.y, 3),
                        "dist_to_goal": round(dist_to_goal, 3)})

        if dist_to_goal <= 0.3 and collision_count == 0:
            status = "success"
            break
        if collision_count > 0:
            break

    final_state = get_robot_state(model, data)
    final_dist = float(np.hypot(goal[0] - final_state.x, goal[1] - final_state.y))

    metrics = {
        "simulator": "mujoco",
        "mujoco_version": mujoco.__version__,
        "skillId": "k1_navigate_avoid_obstacles",
        "goal": {"x": goal_x, "y": goal_y},
        "status": status,
        "final_pose": {"x": final_state.x, "y": final_state.y, "yaw": final_state.yaw},
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

    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal_x", type=float, default=5.0)
    parser.add_argument("--goal_y", type=float, default=0.0)
    parser.add_argument("--max_time_sec", type=float, default=60.0)
    args = parser.parse_args()

    result = run(args.goal_x, args.goal_y, args.max_time_sec)
    print(json.dumps(result, indent=2))
