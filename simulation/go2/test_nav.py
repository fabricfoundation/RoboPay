"""Navigation acceptance test.

Three episodes: the competitor-aligned layout, a different obstacle layout,
and the same layout with a different goal. All must reach the goal with zero
collisions; trajectories must differ pairwise (proves policy-driven motion,
not replay). Prints metrics JSON and PASS/FAIL.
"""

import json
import sys

import numpy as np

from go2_nav import run_episode

# Layout aligned with PR #36 for reviewer comparison
LAYOUT_A = [("box", 2.5, 0, 0.5, 0.5, 0.5),
            ("box", 5.0, 1.5, 0.4, 0.4, 0.6),
            ("cylinder", 7.0, -1.0, 0.3, 0.5)]
GOAL_A = (10.0, 0.0)

LAYOUT_B = [("box", 1.8, -0.5, 0.4, 0.4, 0.5),
            ("cylinder", 3.5, 0.8, 0.35, 0.5),
            ("box", 5.5, -1.2, 0.5, 0.3, 0.4)]
GOAL_B = (8.0, -2.0)

GOAL_C = (8.0, 2.5)   # layout A again, different goal

# Stress layouts: a 1.3 m gap dead ahead, and clutter near the goal.
# Both defeated the earlier potential-field policy (local minimum on a
# flat box face / tangent-sign chatter); kept as regressions for A*.
LAYOUT_D = [("box", 3.0, 1.15, 0.5, 0.5, 0.5),
            ("box", 3.0, -1.15, 0.5, 0.5, 0.5)]
GOAL_D = (6.0, 0.0)
LAYOUT_E = [("box", 2.0, 0.3, 0.4, 0.4, 0.5),
            ("cylinder", 4.0, -0.6, 0.35, 0.5),
            ("box", 5.5, 0.8, 0.4, 0.4, 0.5),
            ("cylinder", 6.5, -0.3, 0.3, 0.5)]
GOAL_E = (7.5, 0.5)


def traj_gap(t1, t2):
    """Max pointwise separation over the common prefix."""
    n = min(len(t1), len(t2))
    a, b = np.array(t1[:n]), np.array(t2[:n])
    return float(np.max(np.linalg.norm(a - b, axis=1)))


def main():
    episodes = [
        ("A_competitor_layout", LAYOUT_A, GOAL_A),
        ("B_other_layout", LAYOUT_B, GOAL_B),
        ("C_other_goal", LAYOUT_A, GOAL_C),
        ("D_narrow_gap", LAYOUT_D, GOAL_D),
        ("E_dense_near_goal", LAYOUT_E, GOAL_E),
    ]
    results = {}
    for name, layout, goal in episodes:
        results[name] = run_episode(layout, goal)

    gap_ab = traj_gap(results["A_competitor_layout"]["trajectory"],
                      results["B_other_layout"]["trajectory"])
    gap_ac = traj_gap(results["A_competitor_layout"]["trajectory"],
                      results["C_other_goal"]["trajectory"])
    checks = {
        **{f"{n}_reached": r["reached"] for n, r in results.items()},
        **{f"{n}_no_collision": r["collisions"] == 0 for n, r in results.items()},
        "trajectories_differ_AB": gap_ab > 1.0,
        "trajectories_differ_AC": gap_ac > 1.0,
    }
    summary = {n: {k: v for k, v in r.items() if k != "trajectory"}
               for n, r in results.items()}
    print(json.dumps({"episodes": summary,
                      "traj_gap_AB_m": round(gap_ab, 2),
                      "traj_gap_AC_m": round(gap_ac, 2),
                      "checks": checks}, indent=1))
    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
