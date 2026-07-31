"""
Sim-to-Sim validation for the Booster K1 obstacle-avoidance navigation skill.

Runs the same scenario (goal, obstacles, policy) in both MuJoCo and
Webots, then compares the resulting metrics.json files against
explicit tolerances. Exits non-zero if any metric disagrees beyond
tolerance, so this can be wired into CI.

Usage:
    python3 sim_to_sim_validate.py --goal_x 5.0 --goal_y 0.0 --max_time_sec 60
    python3 sim_to_sim_validate.py --skip-run   # just compare existing results/*.json
"""
import argparse
import json
import os
import subprocess
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
MUJOCO_RUNNER = os.path.join(THIS_DIR, "mujoco", "runner.py")
MUJOCO_RESULTS = os.path.join(THIS_DIR, "mujoco", "results", "metrics.json")
WEBOTS_RESULTS = os.path.join(THIS_DIR, "webots", "results", "metrics.json")

TOLERANCES = {
    "distance_to_goal_m": {"abs": 0.15, "rel": None},
    "path_length_m": {"abs": None, "rel": 0.15},
    "collision_count": {"abs": 0, "rel": None},
    "status": {"exact": True},
}


def run_mujoco(goal_x, goal_y, max_time_sec):
    print(f"[sim-to-sim] Running MuJoCo: goal=({goal_x},{goal_y}) max_time={max_time_sec}s")
    subprocess.run(
        [sys.executable, MUJOCO_RUNNER,
         "--goal_x", str(goal_x), "--goal_y", str(goal_y),
         "--max_time_sec", str(max_time_sec)],
        check=True, cwd=os.path.join(THIS_DIR, "mujoco"),
    )


def load(path, label):
    if not os.path.exists(path):
        print(f"[sim-to-sim] ERROR: {label} results not found at {path}")
        sys.exit(2)
    with open(path) as f:
        return json.load(f)


def compare(mujoco, webots):
    failures = []

    if mujoco["goal"] != webots["goal"]:
        failures.append(f"Different goals compared: mujoco={mujoco['goal']} webots={webots['goal']}")

    if mujoco["status"] != webots["status"]:
        failures.append(f"status mismatch: mujoco={mujoco['status']} webots={webots['status']}")

    for field, tol in TOLERANCES.items():
        if field == "status":
            continue
        mv, wv = mujoco.get(field), webots.get(field)
        if mv is None or wv is None:
            failures.append(f"{field}: missing in one of the results (mujoco={mv}, webots={wv})")
            continue

        if tol.get("abs") == 0:
            if mv != wv:
                failures.append(f"{field}: must match exactly, mujoco={mv} webots={wv}")
            continue

        if tol.get("abs") is not None:
            diff = abs(mv - wv)
            if diff > tol["abs"]:
                failures.append(f"{field}: |{mv} - {wv}| = {diff:.4f} > abs tolerance {tol['abs']}")

        if tol.get("rel") is not None:
            denom = max(abs(mv), abs(wv), 1e-6)
            rel_diff = abs(mv - wv) / denom
            if rel_diff > tol["rel"]:
                failures.append(
                    f"{field}: relative diff {rel_diff:.2%} > tolerance {tol['rel']:.0%} "
                    f"(mujoco={mv}, webots={wv})"
                )

    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal_x", type=float, default=5.0)
    parser.add_argument("--goal_y", type=float, default=0.0)
    parser.add_argument("--max_time_sec", type=float, default=60.0)
    parser.add_argument("--skip-run", action="store_true",
                         help="Skip running MuJoCo/Webots, just compare existing results files. "
                              "Webots run requires a manually-started extern-controller session "
                              "(see docs/README.md), so it cannot be auto-launched here.")
    args = parser.parse_args()

    if not args.skip_run:
        run_mujoco(args.goal_x, args.goal_y, args.max_time_sec)
        print("[sim-to-sim] NOTE: Webots run must be started separately "
              "(extern controller mode) -- see docs/README.md for the exact "
              "two-terminal procedure. Re-run this script with --skip-run "
              "after both results/metrics.json files exist.")

    mujoco = load(MUJOCO_RESULTS, "MuJoCo")
    webots = load(WEBOTS_RESULTS, "Webots")

    print("\n[sim-to-sim] MuJoCo :", json.dumps({k: mujoco[k] for k in
          ("status", "distance_to_goal_m", "path_length_m", "collision_count", "sim_time_sec")}, indent=2))
    print("[sim-to-sim] Webots :", json.dumps({k: webots[k] for k in
          ("status", "distance_to_goal_m", "path_length_m", "collision_count", "sim_time_sec")}, indent=2))

    failures = compare(mujoco, webots)

    if failures:
        print("\n[sim-to-sim] FAILED:")
        for f in failures:
            print("  -", f)
        sys.exit(1)

    print("\n[sim-to-sim] PASSED: MuJoCo and Webots agree within tolerance.")
    sys.exit(0)


if __name__ == "__main__":
    main()
