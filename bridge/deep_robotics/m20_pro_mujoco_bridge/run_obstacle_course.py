"""Run the bounded M20 obstacle-navigation skill without Tunnel or payment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from m20_pro_mujoco_bridge.contracts import DRIVE_SKILL, validate_action
from m20_pro_mujoco_bridge.runtime import run_drive_episode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal-distance-m", type=float, default=1.35)
    parser.add_argument("--wheel-speed-rad-s", type=float, default=4.0)
    parser.add_argument("--max-duration-sec", type=float, default=16.0)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--viewer", action="store_true")
    parser.add_argument(
        "--viewer-hold-seconds",
        type=float,
        default=0.0,
        help="keep the final measured simulator state visible after the episode",
    )
    args = parser.parse_args()
    request = validate_action(
        "lynx-m20-pro-sim-01",
        DRIVE_SKILL,
        DRIVE_SKILL,
        {
            "goalDistanceM": args.goal_distance_m,
            "wheelSpeedRadS": args.wheel_speed_rad_s,
            "maxDurationSec": args.max_duration_sec,
        },
    )
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
