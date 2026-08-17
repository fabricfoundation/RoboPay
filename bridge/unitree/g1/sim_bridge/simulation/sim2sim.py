"""Sim-to-sim validation: run the same policy in MuJoCo and in Drake.

Required by the Tier 1 criteria. The check is not "do the two engines produce
identical numbers" -- they will not, and a test that demanded that would only
measure solver tolerances. What has to hold is that the *skill* is a property
of the plan rather than of one engine's contact model:

  * both engines must reach the same verdict on the same request, and
  * both must leave the puck in about the same place.

A disagreement here would mean the policy is exploiting something specific to
one simulator, which is exactly what the requirement exists to catch.

Run it directly:

    python -m sim_bridge.simulation.sim2sim --puck 0.34 -0.20 --goal 0.44 -0.04
"""

from __future__ import annotations

import argparse
import json
import sys

from ..g1.mapper import TaskSpec
from .drake_env import DrakeG1Env
from .metrics import RunMetrics, compare
from .runner import TaskRunner, mujoco_factory


def drake_factory(px: float, py: float, gx: float, gy: float) -> DrakeG1Env:
    return DrakeG1Env(target_x=px, target_y=py, goal_x=gx, goal_y=gy)


def run_both(task: TaskSpec) -> tuple[RunMetrics, RunMetrics]:
    """Execute one task in each engine and return both metric sets."""
    mujoco = TaskRunner(factory=mujoco_factory, engine="mujoco").run(task)
    drake = TaskRunner(factory=drake_factory, engine="drake").run(task)
    return mujoco, drake


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--puck", nargs=2, type=float, default=[0.34, -0.20],
                        metavar=("X", "Y"))
    parser.add_argument("--goal", nargs=2, type=float, default=[0.44, -0.04],
                        metavar=("X", "Y"))
    parser.add_argument("--tolerance", type=float, default=None,
                        help="how far apart the two final puck poses may be "
                             "(default: twice the task's own goal tolerance)")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)

    task = TaskSpec(
        skill_id="push_to_target",
        puck_xy=(args.puck[0], args.puck[1]),
        goal_xy=(args.goal[0], args.goal[1]),
    )
    mujoco, drake = run_both(task)
    verdict = compare(mujoco, drake, tolerance=args.tolerance)
    tolerance = verdict["toleranceM"]

    if args.json:
        print(json.dumps(
            {"mujoco": mujoco.to_json(), "drake": drake.to_json(),
             "comparison": verdict},
            indent=2,
        ))
        return 0 if verdict["agrees"] else 1

    print(f"task: puck {tuple(args.puck)} -> goal {tuple(args.goal)}")
    print()
    for metrics in (mujoco, drake):
        status = "success" if metrics.success else f"FAILED ({metrics.reason})"
        print(f"  {metrics.engine:<8} {status}")
        print(f"           puck end      {tuple(round(v, 4) for v in metrics.puck_end)}")
        print(f"           displacement  {metrics.displacement:.4f} m")
        print(f"           to goal       {metrics.final_distance:.4f} m")
        print(f"           peak contacts {metrics.peak_contacts}"
              f"  sim {metrics.sim_seconds:.2f}s")
    print()
    print(f"  verdicts match : {verdict['verdictMatches']}")
    print(f"  puck end gap   : {verdict['puckEndGapM']:.4f} m "
          f"(tolerance {tolerance:.4f})")
    print(f"  AGREES         : {verdict['agrees']}")
    return 0 if verdict["agrees"] else 1


if __name__ == "__main__":
    sys.exit(main())
