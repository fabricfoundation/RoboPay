"""Sim-to-sim validation across MuJoCo, PyBullet and Webots.

Every engine runs the *same* pinned Atlas v4 URDF, the *same* shelf geometry from
:mod:`task` and the *same* :class:`~.control_core.ShelfInspectionController`.
The comparison therefore isolates the physics engine, which is the only thing
that differs between the runs.

Webots is optional: it is only included when a Webots installation is present.
Its absence is reported explicitly rather than being silently swallowed — a
missing engine never turns a failed comparison into a passing one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .task import INSPECTION_TARGETS

#: Every engine must complete every target for the comparison to pass.
REQUIRED_TARGETS = len(INSPECTION_TARGETS)
#: Largest tolerated spread in mean end-effector error between engines.
MAX_MEAN_ERROR_SPREAD_M = 0.05
#: Largest tolerated spread in episode duration between engines.
MAX_DURATION_SPREAD_S = 5.0


def _summary(result: dict) -> dict:
    return {
        "engine": result["simulator_engine"],
        "status": result["status"],
        "targets_completed": result["targets_completed"],
        "targets_total": result["targets_total"],
        "mean_position_error_m": result["mean_position_error_m"],
        "max_position_error_m": result["max_position_error_m"],
        "min_pelvis_height_m": result["min_pelvis_height_m"],
        "fall_detected": result["fall_detected"],
        "shelf_contacts": result["shelf_contacts"],
        "sim_duration_seconds": result["sim_duration_seconds"],
        "per_target": result["policy_state"]["per_target"],
    }


def _spread(values: list[float]) -> float:
    return round(max(values) - min(values), 5)


def run_sim2sim(max_duration: float | None = None, include_webots: bool = True) -> dict:
    """Run the task on every available engine and compare the outcomes."""
    from .pybullet_runner import run_inspection as run_pybullet
    from .runner import run_inspection as run_mujoco

    kwargs = {} if max_duration is None else {"max_duration_seconds": max_duration}
    runs = [run_mujoco(**kwargs), run_pybullet(**kwargs)]

    webots_status = "not_requested"
    if include_webots:
        from .webots_env import run_webots_episode, webots_available

        if webots_available():
            runs.append(run_webots_episode(**kwargs))
            webots_status = "ran"
        else:
            webots_status = "unavailable_no_webots_installation"

    summaries = [_summary(result) for result in runs]
    all_complete = all(s["targets_completed"] == REQUIRED_TARGETS for s in summaries)
    none_fell = all(not s["fall_detected"] for s in summaries)
    no_contacts = all(s["shelf_contacts"] == 0 for s in summaries)
    error_spread = _spread([s["mean_position_error_m"] or 0.0 for s in summaries])
    duration_spread = _spread([s["sim_duration_seconds"] for s in summaries])

    consistent = (
        all_complete
        and none_fell
        and no_contacts
        and error_spread <= MAX_MEAN_ERROR_SPREAD_M
        and duration_spread <= MAX_DURATION_SPREAD_S
    )

    return {
        "validation_type": "sim2sim",
        "robot_model": runs[0]["robot_model"],
        "model_source": runs[0]["model_source"],
        "policy_id": runs[0]["policy_id"],
        "engines": [s["engine"] for s in summaries],
        "webots": webots_status,
        "runs": summaries,
        "consistency": {
            "all_engines_completed_all_targets": all_complete,
            "no_engine_reported_a_fall": none_fell,
            "no_engine_reported_shelf_contact": no_contacts,
            "mean_position_error_spread_m": error_spread,
            "mean_position_error_spread_limit_m": MAX_MEAN_ERROR_SPREAD_M,
            "duration_spread_s": duration_spread,
            "duration_spread_limit_s": MAX_DURATION_SPREAD_S,
        },
        "verdict": "PASS" if consistent else "FAIL",
        "full_results": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas sim-to-sim validation.")
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--no-webots", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    result = run_sim2sim(args.max_duration, include_webots=not args.no_webots)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
