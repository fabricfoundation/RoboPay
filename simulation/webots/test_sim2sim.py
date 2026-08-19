"""Sim-to-Sim acceptance test (A5): MuJoCo vs Webots, same policy stack.

Runs identical navigation tasks in both simulators and checks that both
succeed and agree. Writes sim2sim_report.json and prints PASS/FAIL.
"""

import json
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "go2"))

from go2_nav import run_episode          # noqa: E402  (MuJoCo side)
from make_world import make_world        # noqa: E402
from run_webots import run_world         # noqa: E402

TASKS = {
    "A_competitor_layout": {
        "obstacles": [["box", 2.5, 0, 0.5, 0.5, 0.5],
                      ["box", 5.0, 1.5, 0.4, 0.4, 0.6],
                      ["cylinder", 7.0, -1.0, 0.3, 0.5]],
        "goal": (10.0, 0.0),
    },
    "B_other_layout": {
        "obstacles": [["box", 1.8, -0.5, 0.4, 0.4, 0.5],
                      ["cylinder", 3.5, 0.8, 0.35, 0.5],
                      ["box", 5.5, -1.2, 0.5, 0.3, 0.4]],
        "goal": (8.0, -2.0),
    },
}


def resample(traj, n=50):
    """Resample a trajectory to n points, evenly spaced along its length."""
    pts = np.array(traj)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0], np.cumsum(seg)])
    grid = np.linspace(0, s[-1], n)
    return np.stack([np.interp(grid, s, pts[:, 0]),
                     np.interp(grid, s, pts[:, 1])], axis=1)


def compare(mj, wb):
    a, b = resample(mj["trajectory"]), resample(wb["trajectory"])
    gaps = np.linalg.norm(a - b, axis=1)
    return {
        "mujoco": {k: v for k, v in mj.items() if k != "trajectory"},
        "webots": {k: v for k, v in wb.items() if k != "trajectory"},
        "traj_mean_gap_m": round(float(np.mean(gaps)), 3),
        "traj_max_gap_m": round(float(np.max(gaps)), 3),
        "time_ratio": round(wb["time_s"] / mj["time_s"], 3),
        "path_length_ratio": round(wb["path_length_m"] / mj["path_length_m"], 3),
    }


def main():
    report, checks = {}, {}
    for name, task in TASKS.items():
        obstacles = [tuple(ob) for ob in task["obstacles"]]
        print(f"--- {name}: mujoco ---", flush=True)
        mj = run_episode(obstacles, task["goal"])
        print(f"--- {name}: webots ---", flush=True)
        make_world(name, task["obstacles"], task["goal"])
        wb = run_world(name)
        cmp = compare(mj, wb)
        report[name] = cmp
        checks[f"{name}_both_reached"] = mj["reached"] and wb["reached"]
        checks[f"{name}_no_collisions"] = \
            mj["collisions"] == 0 and wb["collisions"] == 0
        checks[f"{name}_paths_agree"] = cmp["traj_max_gap_m"] < 1.5
        checks[f"{name}_duration_agrees"] = 0.5 < cmp["time_ratio"] < 2.0

    report["checks"] = checks
    out = HERE / "sim2sim_report.json"
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
