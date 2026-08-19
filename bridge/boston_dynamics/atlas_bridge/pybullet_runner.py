"""Run one Atlas shelf-inspection episode in PyBullet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .episode import run_episode
from .pybullet_env import AtlasInspectionPyBulletEnvironment
from .task import EPISODE_BUDGET_S


def run_inspection(max_duration_seconds: float = EPISODE_BUDGET_S, gui: bool = False) -> dict:
    """Execute the inspection skill in PyBullet and return its metrics."""
    environment = AtlasInspectionPyBulletEnvironment(gui=gui)
    try:
        return run_episode(
            environment, engine="PyBullet", max_duration_seconds=max_duration_seconds
        )
    finally:
        environment.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Atlas inspection episode in PyBullet.")
    parser.add_argument("--max-duration", type=float, default=EPISODE_BUDGET_S)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    result = run_inspection(args.max_duration, gui=args.gui)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
