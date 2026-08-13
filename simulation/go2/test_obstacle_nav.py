"""Test obstacle navigation skill on MuJoCo Go2.

Drives the real controller path (``Go2Controller.execute("navigate_obstacle",
...)``) so the success/failure decision is the one the paid action actually
reports, not a re-implementation of the loop in the test. Obstacle geoms are
injected into the scene (``obstacle_world.build_obstacle_world``) so obstacle
contact is detected by the physics engine from real MuJoCo contact pairs.

Asserts on the reported ActionResult:
- status == "success" and the goal is reached with zero obstacle contacts
- waypointsReached == totalWaypoints (start is not counted as a waypoint)
- final goal distance and minimum clearance within tolerance

Writes simulation/docs/obstacle_nav_report.json. Exits nonzero on failure.
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
SIM_ROOT = HERE.parent
sys.path.insert(0, str(SIM_ROOT / "go2"))

from go2_control import Go2Controller  # noqa: E402
from obstacle_world import build_obstacle_world  # noqa: E402

TOLERANCE_GOAL = 0.20          # 20 cm
# Descending course inside the calibrated steering range of the gait
# (-21.7 deg .. ~0 deg, see the STEER_TABLE note in go2_control.py): each
# segment is a gentle downward slope and each obstacle sits just inside the
# nominal segment line, so the potential-field repulsion must actively steer
# the robot around it.
WAYPOINTS = [
    {"x": 1.2, "y": -0.20},
    {"x": 2.4, "y": -0.55},
    {"x": 3.6, "y": -0.85},
]
GOAL = {"goalX": 4.4, "goalY": -0.95}


def resolve_scene():
    env = SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "scene.xml"
    if not env.exists():
        env = SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "go2.xml"
    if not env.exists():
        print(f"Model not found at {env}; run simulation/setup.sh")
        sys.exit(1)
    return str(env)


def main():
    scene = resolve_scene()
    world = build_obstacle_world(scene)
    ctl = Go2Controller(model_path=world)
    ctl.reset(settle=True)

    print(f"Home body Z: {ctl.home_body_z:.4f}")
    print(f"Obstacle geoms in world: {len(ctl._obstacle_geoms)}")

    # Record the real physics trajectory so the course map below is drawn
    # from the actual simulated path, not from a sketch.
    trajectory = []

    def record(controller):
        trajectory.append((controller.data.qpos[0], controller.data.qpos[1]))

    ctl.set_on_step(record)
    params = {"goalX": GOAL["goalX"], "goalY": GOAL["goalY"],
              "waypoints": WAYPOINTS}
    result = ctl.execute("navigate_obstacle", params)
    m = result.metrics

    print(f"\n=== Obstacle Navigation Results ===")
    print(f"Status:            {result.status}")
    print(f"Waypoints reached: {m.get('waypointsReached')}/{m.get('totalWaypoints')}")
    print(f"Path length:       {m.get('pathLengthM')} m")
    print(f"Min clearance:     {m.get('minClearanceM')} m")
    print(f"Contacts:          {m.get('contacts')}")
    print(f"Final goal dist:   {m.get('finalGoalDistanceM')} m")
    print(f"Heading error:     {m.get('headingErrorDeg')} deg")

    success = (
        result.status == "success"
        and m.get("contacts") == 0
        and m.get("finalGoalDistanceM", 9e9) <= TOLERANCE_GOAL
        and m.get("waypointsReached") == m.get("totalWaypoints")
        and m.get("minClearanceM", 0.0) > 0
    )

    report = {
        "skill": "navigate_obstacle",
        "success": success,
        "status": result.status,
        "message": result.message,
        "waypoints_reached": m.get("waypointsReached"),
        "total_waypoints": m.get("totalWaypoints"),
        "path_length_m": m.get("pathLengthM"),
        "min_clearance_m": m.get("minClearanceM"),
        "contacts": m.get("contacts"),
        "final_goal_distance_m": m.get("finalGoalDistanceM"),
        "heading_error_deg": m.get("headingErrorDeg"),
        "tolerance_goal_m": TOLERANCE_GOAL,
    }
    out = HERE.parent / "docs" / "obstacle_nav_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"Report written to {out}")

    write_course_map(trajectory, WAYPOINTS, GOAL)
    print(f"Course map written to {out.parent / 'obstacle_course_map.svg'}")

    if success:
        print("RESULT: PASS")
        sys.exit(0)
    print("RESULT: FAIL")
    sys.exit(1)


def write_course_map(trajectory, waypoints, goal, sample_every=40):
    """Draw the actual physics path over the static course as an SVG."""
    from obstacle_world import OBSTACLES

    xmin, xmax, ymin, ymax = -0.6, 5.0, -1.4, 0.6
    width, height = 780, 340

    def sx(x):
        return (x - xmin) / (xmax - xmin) * width

    def sy(y):
        return (y - ymax) / (ymin - ymax) * height

    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'viewBox="0 0 {width} {height}" width="{width}" '
                 f'height="{height}">')
    parts.append('<rect x="0" y="0" width="100%" height="100%" fill="#fbfbfb"/>')

    for i, (ox, oy, r) in enumerate(OBSTACLES):
        cx, cy = sx(ox), sy(oy)
        rr = r * (width / (xmax - xmin))
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rr:.1f}" '
            f'fill="rgba(200,60,60,0.35)" stroke="#c43c3c" stroke-width="2"/>')
        parts.append(
            f'<text x="{cx:.1f}" y="{cy - rr - 6:.1f}" font-size="13" '
            f'fill="#a33">obs_{i} (r={r})</text>')

    for i, wp in enumerate(waypoints):
        cx, cy = sx(wp["x"]), sy(wp["y"])
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="6" fill="#2f6fde"/>')
        parts.append(
            f'<text x="{cx + 8:.1f}" y="{cy - 6:.1f}" font-size="12" '
            f'fill="#2f6fde">wp{i + 1}</text>')

    gx, gy = sx(goal["goalX"]), sy(goal["goalY"])
    parts.append(
        f'<path d="M {gx - 9} {gy - 9} L {gx + 9} {gy + 9} M {gx + 9} '
        f'{gy - 9} L {gx - 9} {gy + 9}" stroke="#1c8a3c" stroke-width="3"/>')
    parts.append(
        f'<text x="{gx + 10:.1f}" y="{gy - 6:.1f}" font-size="13" '
        f'fill="#1c8a3c">goal</text>')

    if trajectory:
        pts = trajectory[::sample_every] + [trajectory[-1]]
        path = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in pts)
        parts.append(
            f'<polyline points="{path}" fill="none" stroke="#333" '
            f'stroke-width="2.5" stroke-linejoin="round" '
            f'stroke-linecap="round"/>')
        start_x, start_y = sx(trajectory[0][0]), sy(trajectory[0][1])
        end_x, end_y = sx(trajectory[-1][0]), sy(trajectory[-1][1])
        parts.append(
            f'<circle cx="{start_x:.1f}" cy="{start_y:.1f}" r="7" '
            f'fill="#333"/><text x="{start_x + 9:.1f}" y="{start_y - 6:.1f}" '
            f'font-size="13" fill="#333">start</text>')
        parts.append(
            f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="7" fill="#1c8a3c"/>')

    parts.append(
        f'<text x="12" y="{height - 12}" font-size="12" fill="#666">'
        f'simulated physics path (MuJoCo, sample every {sample_every} steps)'
        f'</text>')
    parts.append("</svg>")

    out = HERE.parent / "docs" / "obstacle_course_map.svg"
    out.write_text("\n".join(parts), encoding="utf-8")


if __name__ == "__main__":
    main()
