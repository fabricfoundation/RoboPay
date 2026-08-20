"""Run one Atlas shelf-inspection episode in MuJoCo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from .episode import run_episode
from .model import joint_efforts
from .mujoco_env import AtlasInspectionEnvironment
from .task import EPISODE_BUDGET_S


def run_inspection(
    max_duration_seconds: float = EPISODE_BUDGET_S,
    stop_requested: Callable[[], bool] | None = None,
) -> dict:
    """Execute the inspection skill in MuJoCo and return its metrics."""
    return run_episode(
        AtlasInspectionEnvironment(),
        engine="MuJoCo",
        max_duration_seconds=max_duration_seconds,
        stop_requested=stop_requested,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Atlas shelf-inspection episode.")
    parser.add_argument("--max-duration", type=float, default=EPISODE_BUDGET_S)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    joint_efforts()  # fetches the pinned description on first run
    result = run_inspection(args.max_duration)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
