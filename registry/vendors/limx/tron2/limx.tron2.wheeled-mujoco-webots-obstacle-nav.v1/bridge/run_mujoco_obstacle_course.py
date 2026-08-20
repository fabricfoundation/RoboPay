from __future__ import annotations

import argparse
import json
from pathlib import Path

from limx_tron2_sim.contracts import NAVIGATION_SKILL, NavigationRequest
from limx_tron2_sim.runtime import run_mujoco_episode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer", action="store_true")
    parser.add_argument("--hold-seconds", type=float, default=0.0)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = run_mujoco_episode(
        NavigationRequest(NAVIGATION_SKILL),
        viewer=args.viewer,
        viewer_hold_seconds=max(0.0, args.hold_seconds),
    )
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
