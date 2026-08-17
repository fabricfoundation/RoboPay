"""Run the bounded physical X30 inspection lane without Tunnel or payment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from x30_pro_mujoco_bridge.contracts import DRIVE_SKILL, validate_action
from x30_pro_mujoco_bridge.runtime import run_drive_episode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--viewer", action="store_true")
    parser.add_argument(
        "--viewer-hold-seconds",
        type=float,
        default=0.0,
        help="keep the final measured simulator state visible after the episode",
    )
    args = parser.parse_args()
    request = validate_action("x30-pro-sim-01", DRIVE_SKILL, DRIVE_SKILL, {})
    result = run_drive_episode(
        request,
        viewer=args.viewer,
        viewer_hold_seconds=max(0.0, args.viewer_hold_seconds),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
