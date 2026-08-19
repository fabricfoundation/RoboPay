"""
Webots controller for the Deep Robotics M20 Pro obstacle-navigation skill.

Runs the SAME potential-field navigation policy as
simulation/runners/m20_pro_runner.py (MuJoCo), driving a proxy robot body
in Webots instead. This is a Sim-to-Sim consistency check: the same
policy code, given the same obstacle layout and goal, must produce the
same high-level outcome (goal_reached, zero collisions) regardless of
which physics engine executes it.
"""

import json
import math
import os

from controller import Supervisor

VX_RANGE = (0.0, 1.5)
WZ_RANGE = (-1.0, 1.0)
GOAL_TOLERANCE_M = 0.35
SAFE_OBSTACLE_DIST_M = 0.8
TARGET_XY = (8.0, 0.0)
MAX_SIM_SECONDS = 30.0

BASE_HALF_EXTENT = (0.20, 0.12)
OBSTACLE_HALF_EXTENT = {
    "obstacle_1": (0.15, 0.15),
    "obstacle_2": (0.15, 0.15),
    "obstacle_3": (0.15, 0.15),
}


def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def main():
    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())

    self_node = robot.getSelf()
    translation_field = self_node.getField("translation")
    rotation_field = self_node.getField("rotation")

    obstacle_names = ["obstacle_1", "obstacle_2", "obstacle_3"]
    obstacle_nodes = [robot.getFromDef(name.upper()) for name in obstacle_names]

    target_xy = TARGET_XY
    start_pos = translation_field.getSFVec3f()
    start_xy = (start_pos[0], start_pos[1])

    path_length = 0.0
    prev_xy = start_xy
    real_collisions = 0
    avoidance_events = 0
    status = "running"
    steps = 0
    max_steps = int(MAX_SIM_SECONDS * 1000 / timestep)

    for steps in range(max_steps):
        if robot.step(timestep) == -1:
            status = "stopped"
            break

        pos = translation_field.getSFVec3f()
        base_xy = (pos[0], pos[1])
        rot = rotation_field.getSFRotation()
        yaw = rot[3] if abs(rot[2]) > 0.5 else 0.0

        to_goal = (target_xy[0] - base_xy[0], target_xy[1] - base_xy[1])
        dist_to_goal = math.hypot(*to_goal)

        if dist_to_goal < GOAL_TOLERANCE_M:
            status = "goal_reached"
            break

        heading_err = math.atan2(to_goal[1], to_goal[0]) - yaw
        heading_err = math.atan2(math.sin(heading_err), math.cos(heading_err))

        vx = clamp(dist_to_goal, VX_RANGE[0], VX_RANGE[1])
        wz_goal = clamp(heading_err * 1.5, WZ_RANGE[0], WZ_RANGE[1])

        repulse = 0.0
        min_obs_dist = float("inf")
        avoidance_triggered = False
        for node in obstacle_nodes:
            if node is None:
                continue
            obs_pos = node.getField("translation").getSFVec3f()
            vec = (base_xy[0] - obs_pos[0], base_xy[1] - obs_pos[1])
            obs_dist = math.hypot(*vec)
            min_obs_dist = min(min_obs_dist, obs_dist)
            if obs_dist < SAFE_OBSTACLE_DIST_M:
                side = math.atan2(vec[1], vec[0]) - yaw
                side = math.atan2(math.sin(side), math.cos(side))
                strength = (SAFE_OBSTACLE_DIST_M - obs_dist) / SAFE_OBSTACLE_DIST_M
                repulse += strength * (1.0 if side > 0 else -1.0)

        if min_obs_dist < SAFE_OBSTACLE_DIST_M:
            avoidance_triggered = True
            vx *= 0.6
            avoidance_events += 1

        wz = clamp(wz_goal + repulse, WZ_RANGE[0], WZ_RANGE[1])

        dt = timestep / 1000.0
        new_yaw = yaw + wz * dt
        new_x = base_xy[0] + vx * math.cos(new_yaw) * dt
        new_y = base_xy[1] + vx * math.sin(new_yaw) * dt
        translation_field.setSFVec3f([new_x, new_y, pos[2]])
        rotation_field.setSFRotation([0, 0, 1, new_yaw])

        new_xy = (new_x, new_y)
        path_length += math.hypot(new_xy[0] - prev_xy[0], new_xy[1] - prev_xy[1])
        prev_xy = new_xy

        for name, node in zip(obstacle_names, obstacle_nodes):
            if node is None:
                continue
            obs_pos = node.getField("translation").getSFVec3f()
            ox, oy = OBSTACLE_HALF_EXTENT[name]
            overlap_x = abs(new_xy[0] - obs_pos[0]) < (BASE_HALF_EXTENT[0] + ox)
            overlap_y = abs(new_xy[1] - obs_pos[1]) < (BASE_HALF_EXTENT[1] + oy)
            if overlap_x and overlap_y:
                real_collisions += 1
                break
    else:
        status = "timeout"

    final_pos = translation_field.getSFVec3f()
    final_xy = (final_pos[0], final_pos[1])
    displacement = math.hypot(final_xy[0] - start_xy[0], final_xy[1] - start_xy[1])
    remaining = math.hypot(target_xy[0] - final_xy[0], target_xy[1] - final_xy[1])

    result = {
        "engine": "webots",
        "status": status,
        "displacement_m": round(displacement, 4),
        "path_length_m": round(path_length, 4),
        "collisions": real_collisions,
        "target_distance_remaining_m": round(remaining, 4),
        "sim_steps": steps + 1,
        "sim_seconds": round((steps + 1) * timestep / 1000.0, 4),
        "avoidance_events": avoidance_events,
    }

    out_dir = os.path.join(os.path.dirname(__file__), "..", "..", "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "webots_metrics.json"), "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
