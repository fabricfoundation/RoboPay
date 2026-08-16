"""Cross-engine validation that proves the shared policy configuration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runner import run_obstacle_nav
from .webots import run_webots_validation


PACKAGE_ROOT = Path(__file__).resolve().parents[1]

def _shared_fields(result: dict) -> dict:
    state = result.get("policy_state", {})
    initial = result.get("initial_position", {})
    return {
        "policy_id": result.get("policy_id") or state.get("policy_id"),
        "goal": result.get("goal"),
        "route": state.get("route"),
        "parameters": state.get("parameters"),
        "initial_pose": {
            "x": initial.get("x"),
            "y": initial.get("y"),
            "yaw_rad": result.get("initial_yaw_rad"),
        },
    }


def run_sim2sim_validation(timeout_seconds: int = 150) -> dict:
    """Run both physics engines and require one identical policy specification."""

    mujoco_result = run_obstacle_nav()
    webots_result = run_webots_validation(timeout_seconds)
    mujoco_shared = _shared_fields(mujoco_result)
    webots_shared = _shared_fields(webots_result)
    policy_match = mujoco_shared == webots_shared
    success = bool(mujoco_result.get("success")) and bool(webots_result.get("success")) and policy_match
    return {
        "task": "navigate_obstacles_sim2sim",
        "status": "success" if success else "failure",
        "success": success,
        "shared_policy_match": policy_match,
        "shared_policy": mujoco_shared,
        "mujoco": mujoco_result,
        "webots": webots_result,
        "note": (
            "The official Unitree MJCF uses torque motors while the official Unitree URDF "
            "is converted to Webots position motors. The online policy, route, foot-space "
            "gait, state feedback, and success metrics are shared; neither adapter writes "
            "the floating-base pose."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the paired Go2 sim-to-sim validation.")
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=PACKAGE_ROOT / "artifacts" / "sim2sim_result.json",
    )
    args = parser.parse_args()
    result = run_sim2sim_validation(args.timeout)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
