"""Run the same bounded M20 lane request in MuJoCo and Webots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from m20_pro_mujoco_bridge.contracts import DRIVE_SKILL, validate_action
from m20_pro_mujoco_bridge.runtime import run_drive_episode
from m20_pro_mujoco_bridge.webots import run_webots_episode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--viewer",
        action="store_true",
        help="open the real MuJoCo and Webots desktop views instead of headless Webots",
    )
    args = parser.parse_args()
    request = validate_action(
        "lynx-m20-pro-sim-01",
        DRIVE_SKILL,
        DRIVE_SKILL,
        {"goalDistanceM": 1.35, "wheelSpeedRadS": 4.0, "maxDurationSec": 16.0},
    )
    mujoco_result = run_drive_episode(request, viewer=args.viewer)
    webots_result = run_webots_episode(request, viewer=args.viewer)
    success = bool(mujoco_result.get("success") and webots_result.get("success"))
    report = {
        "task": "lynx_m20_pro_dynamic_obstacle_course_sim2sim",
        "status": "success" if success else "failure",
        "success": success,
        "shared_policy": {
            "policy_id": "m20-pro-dynamic-obstacle-yield-v1",
            "goal_distance_m": request.goal_distance_m,
            "wheel_speed_rad_s": request.wheel_speed_rad_s,
            "max_duration_sec": request.max_duration_sec,
            "state_authority": "measured base pose in each simulator",
        },
        "mujoco": mujoco_result,
        "webots": webots_result,
        "note": "MuJoCo uses the vendor MJCF. Webots converts the same locked vendor URDF at runtime; each adds the same profile-owned physical moving obstacle and Spot-style pose-feedback yield policy. The PROTO is not vendor supplied.",
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    artifact = Path(__file__).resolve().parent / "artifacts" / "sim2sim_result.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(rendered + "\n", encoding="utf-8")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
