"""Measure where free-standing Atlas can actually reach without losing balance.

The inspection targets in :mod:`task` are not guesses: this sweep drives the
robot to a grid of candidate points and records, for each one, whether the arm
converged and whether the robot was still standing afterwards.  The resulting
envelope is what the shelf geometry is chosen from, and it is regenerated as
evidence rather than asserted in prose.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .control_core import ShelfInspectionController
from .kinematics import jacobian
from .mujoco_env import AtlasInspectionEnvironment
from .task import FALL_THRESHOLD_M, STANCE_POSE, InspectionTarget

#: Offsets from the settled home end-effector pose, in metres.
FORWARD_OFFSETS = (0.06, 0.12, 0.18, 0.21, 0.24, 0.30)
VERTICAL_OFFSETS = (0.20, 0.10, 0.00, -0.06, -0.12, -0.20)
#: A probe counts as reachable at this accuracy.
REACH_TOLERANCE_M = 0.03
PROBE_BUDGET_S = 12.0


def _probe(offset: np.ndarray) -> dict:
    """Send the arm to one candidate point and report what happened."""
    environment = AtlasInspectionEnvironment()
    observation = environment.reset(dict(STANCE_POSE))

    # Settle into the stance first so the offset is measured from a known pose.
    for _ in range(400):
        observation = environment.step(dict(STANCE_POSE))
    home = environment.end_effector().copy()

    goal = home + offset
    target = InspectionTarget("probe", *goal, tolerance_m=REACH_TOLERANCE_M, hold_steps=120)
    controller = ShelfInspectionController(targets=(target,), budget_seconds=PROBE_BUDGET_S)
    controller.reset(environment.joint_limits())

    while observation["sim_time"] < PROBE_BUDGET_S:
        plan = controller.step(
            environment.end_effector(),
            jacobian(environment.joint_angles(), base_rotation=environment.base_rotation()),
            observation["sim_time"],
        )
        observation = environment.step(plan.joint_targets)
        if environment.fall_detected or controller.finished:
            break

    outcomes = controller.diagnostics()["per_target"]
    reached = bool(outcomes and outcomes[0]["reached"])
    error = outcomes[0]["final_error_m"] if outcomes else None
    return {
        "offset_forward_m": round(float(offset[0]), 3),
        "offset_vertical_m": round(float(offset[2]), 3),
        "goal": [round(float(v), 4) for v in goal],
        "reached": reached,
        "final_error_m": error,
        "min_pelvis_height_m": round(float(environment.min_pelvis_height), 4),
        "fall_detected": environment.fall_detected,
        "shelf_contacts": environment.shelf_contacts,
        "usable": bool(reached and not environment.fall_detected),
    }


def sweep() -> dict:
    probes = [
        _probe(np.array([forward, 0.0, vertical]))
        for vertical in VERTICAL_OFFSETS
        for forward in FORWARD_OFFSETS
    ]
    usable = [probe for probe in probes if probe["usable"]]
    return {
        "validation_type": "reach_envelope",
        "robot_model": "Boston Dynamics Atlas v4",
        "base": "free-standing (no weld, no external support)",
        "tolerance_m": REACH_TOLERANCE_M,
        "fall_threshold_m": FALL_THRESHOLD_M,
        "probe_budget_s": PROBE_BUDGET_S,
        "probes_total": len(probes),
        "probes_usable": len(usable),
        # Reported as the largest block in which *every* probe succeeded, not as
        # a bounding box around scattered successes — a bounding box would imply
        # coverage the sweep never demonstrated.
        "conservative_core": _conservative_core(probes),
        "probes": probes,
    }


def _conservative_core(probes: list[dict]) -> dict | None:
    """Largest forward/vertical block in which every probe is usable."""
    grid = {(p["offset_forward_m"], p["offset_vertical_m"]): p["usable"] for p in probes}
    forwards = sorted({key[0] for key in grid})
    verticals = sorted({key[1] for key in grid})

    best: dict | None = None
    for first_f in range(len(forwards)):
        for last_f in range(first_f, len(forwards)):
            for first_v in range(len(verticals)):
                for last_v in range(first_v, len(verticals)):
                    block = [
                        grid[(forwards[f], verticals[v])]
                        for f in range(first_f, last_f + 1)
                        for v in range(first_v, last_v + 1)
                    ]
                    if not all(block):
                        continue
                    if best is None or len(block) > best["cells"]:
                        best = {
                            "cells": len(block),
                            "forward_range_m": [forwards[first_f], forwards[last_f]],
                            "vertical_range_m": [verticals[first_v], verticals[last_v]],
                        }
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure the Atlas reach envelope.")
    parser.add_argument(
        "--json-output", type=Path, default=Path("docs/evidence/reach-envelope.json")
    )
    args = parser.parse_args()
    result = sweep()
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    core = result["conservative_core"]
    print(f"{result['probes_usable']}/{result['probes_total']} probes usable")
    if core:
        print(f"conservative core: forward {core['forward_range_m']} m, "
              f"vertical {core['vertical_range_m']} m ({core['cells']} probes)")


if __name__ == "__main__":
    main()
