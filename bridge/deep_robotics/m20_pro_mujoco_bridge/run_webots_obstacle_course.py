"""Open the M20 obstacle course in a real-time Webots desktop window."""

from __future__ import annotations

import argparse
import os

from m20_pro_mujoco_bridge.contracts import DRIVE_SKILL, validate_action
from m20_pro_mujoco_bridge.webots import run_webots_episode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--viewer-hold-seconds",
        type=float,
        default=300.0,
        help="keep the completed physical course visible (default: 300)",
    )
    args = parser.parse_args()
    if args.viewer_hold_seconds < 0:
        parser.error("--viewer-hold-seconds must be non-negative")
    os.environ["M20_WEBOTS_VIEWER_HOLD_SECONDS"] = str(args.viewer_hold_seconds)
    request = validate_action(
        "lynx-m20-pro-sim-01",
        DRIVE_SKILL,
        DRIVE_SKILL,
        {"goalDistanceM": 1.35, "wheelSpeedRadS": 4.0, "maxDurationSec": 16.0},
    )
    result = run_webots_episode(request, viewer=True)
    print(result)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
