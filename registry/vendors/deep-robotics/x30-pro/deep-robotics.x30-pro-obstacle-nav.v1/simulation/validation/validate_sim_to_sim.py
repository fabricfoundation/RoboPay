"""
Sim-to-Sim validation for the Deep Robotics X30 Pro obstacle-navigation skill.

Compares the outcome of the SAME potential-field navigation policy running
in two different physics engines (MuJoCo and Webots), given the same goal
and obstacle layout. Consistency here demonstrates the skill is driven by
policy logic, not a simulator-specific scripted trajectory.

Usage:
    python simulation/runners/x30_pro_runner.py   (writes docs/evidence via demo/run_demo.py)
    webots --mode=fast --batch simulation/webots/worlds/x30_pro_obstacle_nav.wbt
    python simulation/validation/validate_sim_to_sim.py
"""

import json
import os
import sys

DISPLACEMENT_TOLERANCE_M = 0.5
REMAINING_TOLERANCE_M = 0.5


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    base_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    mujoco_path = os.path.join(base_dir, "docs", "evidence", "x30_pro_metrics.json")
    webots_path = os.path.join(base_dir, "simulation", "webots", "results", "webots_metrics.json")

    if not os.path.exists(mujoco_path):
        print(f"MISSING: {mujoco_path} -- run demo/run_demo.py first")
        sys.exit(1)
    if not os.path.exists(webots_path):
        print(f"MISSING: {webots_path} -- run the Webots world first")
        sys.exit(1)

    mujoco_metrics = load(mujoco_path)["paid_success"]["metrics"]
    webots_metrics = load(webots_path)

    checks = []

    checks.append((
        "status_matches",
        mujoco_metrics["status"] == "goal_reached" and webots_metrics["status"] == "goal_reached",
        f"mujoco={mujoco_metrics['status']} webots={webots_metrics['status']}",
    ))

    checks.append((
        "zero_collisions_both_engines",
        mujoco_metrics["collisions"] == 0 and webots_metrics["collisions"] == 0,
        f"mujoco={mujoco_metrics['collisions']} webots={webots_metrics['collisions']}",
    ))

    displacement_diff = abs(mujoco_metrics["displacement_m"] - webots_metrics["displacement_m"])
    checks.append((
        "displacement_within_tolerance",
        displacement_diff <= DISPLACEMENT_TOLERANCE_M,
        f"mujoco={mujoco_metrics['displacement_m']} webots={webots_metrics['displacement_m']} diff={displacement_diff:.4f}",
    ))

    remaining_diff = abs(
        mujoco_metrics["target_distance_remaining_m"] - webots_metrics["target_distance_remaining_m"]
    )
    checks.append((
        "remaining_distance_within_tolerance",
        remaining_diff <= REMAINING_TOLERANCE_M,
        f"mujoco={mujoco_metrics['target_distance_remaining_m']} webots={webots_metrics['target_distance_remaining_m']} diff={remaining_diff:.4f}",
    ))

    print("=== Sim-to-Sim Validation: MuJoCo vs Webots ===\n")
    all_passed = True
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}: {detail}")
        all_passed = all_passed and passed

    result = {
        "mujoco_metrics": mujoco_metrics,
        "webots_metrics": webots_metrics,
        "checks": [{"name": n, "passed": p, "detail": d} for n, p, d in checks],
        "overall": "PASS" if all_passed else "FAIL",
    }

    out_dir = os.path.join(base_dir, "docs", "evidence")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "sim_to_sim_validation.json"), "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nOverall: {result['overall']}")
    print(f"Written to docs/evidence/sim_to_sim_validation.json")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
