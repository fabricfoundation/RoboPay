"""Test obstacle navigation skill on MuJoCo Go2.

Verifies the robot can follow a waypoint path while maintaining
static stability and avoiding obstacles. Reports:
- waypoints reached
- path length
- minimum obstacle clearance
- contacts
- final goal distance
- heading error
"""

import math
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
SIM_ROOT = HERE.parent
sys.path.insert(0, str(SIM_ROOT / "go2"))

import mujoco

from go2_control import Go2Controller, HOME

LEGS = ["FL", "FR", "RL", "RR"]
FOOT_GEOM_NAMES = [leg.lower() for leg in LEGS]

# Static obstacle course: 4 waypoints forming a path around obstacles
WAYPOINTS = [
    (0.0, 0.0),    # start
    (1.0, 0.5),    # waypoint 1
    (2.0, 0.0),    # waypoint 2
    (3.0, -0.5),   # waypoint 3
    (4.0, 0.0),    # goal
]

# Obstacle positions (x, y, radius)
OBSTACLES = [
    (1.2, 0.3, 0.25),
    (2.3, -0.2, 0.2),
    (3.1, 0.1, 0.15),
]

TOLERANCE_WAYPOINT = 0.15   # 15 cm
TOLERANCE_GOAL = 0.20       # 20 cm
MAX_CLEARANCE_ERROR = 0.05  # 5 cm


def point_to_segment_dist(px, py, x1, y1, x2, y2):
    """Distance from point to line segment."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    closest_x = x1 + t * dx
    closest_y = y1 + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def check_collision(x, y):
    """Check if position collides with any obstacle."""
    for ox, oy, r in OBSTACLES:
        if math.hypot(x - ox, y - oy) <= r + 0.05:  # 5 cm robot radius
            return True
    return False


def main():
    # Resolve model path
    env = HERE.parent.parent / "models" / "mujoco_menagerie" / "unitree_go2" / "scene.xml"
    if not env.exists():
        env = HERE.parent.parent / "models" / "mujoco_menagerie" / "unitree_go2" / "go2.xml"
    if not env.exists():
        print(f"Model not found at {env}; run simulation/setup.sh")
        sys.exit(1)

    ctl = Go2Controller(model_path=str(env))
    ctl.reset(settle=True)

    print(f"Home body Z: {ctl.home_body_z:.4f}")

    # Navigation parameters
    current_wp = 0
    waypoints_reached = 0
    path_length = 0.0
    min_clearance = 9e9
    contacts = 0
    last_x, last_y = ctl.data.qpos[0], ctl.data.qpos[1]

    max_steps = int(60.0 / ctl.sim_dt)
    goal_x, goal_y = WAYPOINTS[-1]

    for step in range(max_steps):
        # Current position
        x, y = ctl.data.qpos[0], ctl.data.qpos[1]
        path_length += math.hypot(x - last_x, y - last_y)
        last_x, last_y = x, y

        # Check waypoint
        if current_wp < len(WAYPOINTS):
            wx, wy = WAYPOINTS[current_wp]
            if math.hypot(x - wx, y - wy) <= TOLERANCE_WAYPOINT:
                waypoints_reached += 1
                current_wp += 1

        # Check clearance
        for ox, oy, r in OBSTACLES:
            d = math.hypot(x - ox, y - oy) - r
            min_clearance = min(min_clearance, d)

        # Simple steering toward next waypoint/goal
        if current_wp < len(WAYPOINTS):
            target_x, target_y = WAYPOINTS[current_wp]
        else:
            target_x, target_y = goal_x, goal_y

        target_yaw = math.atan2(target_y - y, target_x - x)
        current_yaw = math.atan2(
            2 * (ctl.data.qpos[6] * ctl.data.qpos[3] + ctl.data.qpos[4] * ctl.data.qpos[5]),
            1 - 2 * (ctl.data.qpos[4] ** 2 + ctl.data.qpos[5] ** 2)
        )
        yaw_error = target_yaw - current_yaw
        yaw_error = (yaw_error + math.pi) % (2 * math.pi) - math.pi

        # Static-stability shuffle: differential hip abduction
        s = 1.0 if yaw_error > 0 else -1.0
        amp = min(0.35, 0.1 + 0.8 * abs(yaw_error))
        targets = dict(ctl._hold_commands)
        targets["FL_hip_joint"] = targets.get("FL_hip_joint", 0.0) + s * amp
        targets["FR_hip_joint"] = targets.get("FR_hip_joint", 0.0) + s * amp
        targets["RL_hip_joint"] = targets.get("RL_hip_joint", 0.0) - s * amp
        targets["RR_hip_joint"] = targets.get("RR_hip_joint", 0.0) - s * amp

        # Forward velocity component (bounded)
        forward_vel = min(0.25, 0.15 + 0.1 * abs(yaw_error))
        targets["FL_thigh_joint"] = targets.get("FL_thigh_joint", 0.9) - forward_vel * 0.5
        targets["FR_thigh_joint"] = targets.get("FR_thigh_joint", 0.9) - forward_vel * 0.5
        targets["RL_thigh_joint"] = targets.get("RL_thigh_joint", 0.9) + forward_vel * 0.5
        targets["RR_thigh_joint"] = targets.get("RR_thigh_joint", 0.9) + forward_vel * 0.5

        ctl._apply(targets)
        ctl._hold_commands = targets
        ctl.step()

        # Check collision
        if check_collision(x, y):
            contacts += 1

        # Check goal
        if math.hypot(x - goal_x, y - goal_y) <= TOLERANCE_GOAL:
            break

    # Final metrics
    final_x, final_y = ctl.data.qpos[0], ctl.data.qpos[1]
    final_goal_dist = math.hypot(final_x - goal_x, final_y - goal_y)
    final_yaw = math.atan2(
        2 * (ctl.data.qpos[6] * ctl.data.qpos[3] + ctl.data.qpos[4] * ctl.data.qpos[5]),
        1 - 2 * (ctl.data.qpos[4] ** 2 + ctl.data.qpos[5] ** 2)
    )
    target_yaw = math.atan2(goal_y - final_y, goal_x - final_x)
    heading_error = abs((target_yaw - final_yaw + math.pi) % (2 * math.pi) - math.pi)

    print(f"\n=== Obstacle Navigation Results ===")
    print(f"Waypoints reached: {waypoints_reached}/{len(WAYPOINTS)-1}")
    print(f"Path length: {path_length:.3f} m")
    print(f"Min obstacle clearance: {min_clearance:.3f} m")
    print(f"Contacts: {contacts}")
    print(f"Final goal distance: {final_goal_dist:.3f} m")
    print(f"Heading error: {math.degrees(heading_error):.1f} deg")

    success = (
        waypoints_reached == len(WAYPOINTS) - 1 and
        final_goal_dist <= TOLERANCE_GOAL and
        contacts == 0 and
        min_clearance > 0
    )

    report = {
        "skill": "navigate_obstacle",
        "success": success,
        "waypoints_reached": waypoints_reached,
        "total_waypoints": len(WAYPOINTS) - 1,
        "path_length_m": round(path_length, 3),
        "min_clearance_m": round(min_clearance, 3),
        "contacts": contacts,
        "final_goal_distance_m": round(final_goal_dist, 3),
        "heading_error_deg": round(math.degrees(heading_error), 1),
        "tolerance_waypoint_m": TOLERANCE_WAYPOINT,
        "tolerance_goal_m": TOLERANCE_GOAL,
    }

    import json
    out = HERE.parent / "docs" / "obstacle_nav_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"Report written to {out}")

    if success:
        print("RESULT: PASS")
        sys.exit(0)
    else:
        print("RESULT: FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()