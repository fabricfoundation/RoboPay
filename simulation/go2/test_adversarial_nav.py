"""Adversarial obstacle-navigation tests: honest failure semantics.

The navigation skill must never claim success it did not achieve. These tests
drive the *real* controller path (``Go2Controller.run_navigate_obstacle``)
against two courses that cannot be completed and assert the reported failure:

* an unreachable goal  -> error / TIMEOUT  (goal never reached in budget)
* an obstacle blocking the path -> error / COLLISION (real MuJoCo contact)

Both scenarios use the same potential-field planner and the same MuJoCo
contact detection as the passing course in ``test_obstacle_nav.py``, so the
failure decision is the one the paid action would actually report.

Writes simulation/docs/obstacle_adversarial_report.json. Exits nonzero on
failure.
"""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
SIM_ROOT = HERE.parent
sys.path.insert(0, str(SIM_ROOT / "go2"))

import go2_control  # noqa: E402
from go2_control import Go2Controller  # noqa: E402
from obstacle_world import build_obstacle_world  # noqa: E402


def resolve_scene():
    env = SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "scene.xml"
    if not env.exists():
        env = SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "go2.xml"
    if not env.exists():
        print(f"Model not found at {env}; run simulation/setup.sh")
        sys.exit(1)
    return str(env)


def test_timeout(scene):
    """An unreachable goal must return error / TIMEOUT, never success."""
    world = build_obstacle_world(scene, obstacles=[])
    go2_control.OBSTACLES = []
    ctl = Go2Controller(model_path=world)
    ctl.reset(settle=True)
    result = ctl.run_navigate_obstacle(
        4.0, 2.0, [{"x": 1.5, "y": -0.3}], duration=12.0)
    m = result.metrics
    passed = (
        result.status == "error"
        and result.error.get("code") == "TIMEOUT"
        and m.get("finalGoalDistanceM", 0.0) > 0.20
    )
    return passed, {
        "scenario": "unreachable goal",
        "status": result.status,
        "code": result.error.get("code"),
        "waypoints_reached": m.get("waypointsReached"),
        "total_waypoints": m.get("totalWaypoints"),
        "final_goal_distance_m": m.get("finalGoalDistanceM"),
        "message": result.message,
    }


def test_collision(scene):
    """An obstacle blocking the path must return error / COLLISION via real
    MuJoCo contact pairs, not a distance heuristic."""
    obstacles = [(0.7, 0.0, 0.45)]
    world = build_obstacle_world(scene, obstacles=obstacles)
    go2_control.OBSTACLES = list(obstacles)
    ctl = Go2Controller(model_path=world)
    ctl.reset(settle=True)
    result = ctl.run_navigate_obstacle(2.0, 0.0, [], duration=20.0)
    m = result.metrics
    passed = (
        result.status == "error"
        and result.error.get("code") == "COLLISION"
        and m.get("contacts", 0) > 0
    )
    return passed, {
        "scenario": "blocking obstacle",
        "status": result.status,
        "code": result.error.get("code"),
        "contacts": m.get("contacts"),
        "min_clearance_m": m.get("minClearanceM"),
        "final_goal_distance_m": m.get("finalGoalDistanceM"),
        "message": result.message,
    }


def main():
    scene = resolve_scene()

    ok_timeout, rep_timeout = test_timeout(scene)
    print(f"\n=== Adversarial navigation ===")
    print(f"Unreachable goal:  {rep_timeout['status']} / "
          f"{rep_timeout['code']} (PASS: {ok_timeout})")
    ok_collision, rep_collision = test_collision(scene)
    print(f"Blocking obstacle: {rep_collision['status']} / "
          f"{rep_collision['code']}, contacts={rep_collision['contacts']} "
          f"(PASS: {ok_collision})")

    success = ok_timeout and ok_collision
    report = {
        "skill": "navigate_obstacle",
        "success": success,
        "failure_matrix": [rep_timeout, rep_collision],
    }
    out = HERE.parent / "docs" / "obstacle_adversarial_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"Report written to {out}")

    if success:
        print("RESULT: PASS")
        sys.exit(0)
    print("RESULT: FAIL")
    sys.exit(1)


if __name__ == "__main__":
    main()
