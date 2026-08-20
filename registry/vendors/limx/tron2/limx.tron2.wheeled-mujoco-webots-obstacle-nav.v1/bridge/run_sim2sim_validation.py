from __future__ import annotations

import json
from pathlib import Path

from limx_tron2_sim.contracts import NAVIGATION_SKILL, NavigationRequest
from limx_tron2_sim.course import OBSTACLES, WAYPOINTS
from limx_tron2_sim.model import PROFILE_ROOT
from limx_tron2_sim.runtime import run_mujoco_episode
from limx_tron2_sim.webots import run_webots_episode


def main() -> int:
    request = NavigationRequest(NAVIGATION_SKILL)
    mujoco_result = run_mujoco_episode(request)
    webots_result = run_webots_episode(request)
    checks = {
        "both_succeeded": bool(mujoco_result["success"] and webots_result["success"]),
        "same_model_variant": mujoco_result["model_variant"] == "WF_TRON2A" and "WF_TRON2A" in webots_result["model_variant"],
        "same_waypoint_count": mujoco_result["waypoints_completed"] == webots_result["waypoints_completed"] == len(WAYPOINTS),
        "all_obstacles_detected": len(mujoco_result["detected_obstacles"]) == len(webots_result["detected_obstacles"]) == len(OBSTACLES),
        "no_collision": not mujoco_result["collision"] and not webots_result["collision"],
        "measured_goal_reached": mujoco_result["goal_distance_m"] <= 0.34 and webots_result["goal_distance_m"] <= 0.34,
    }
    report = {
        "schema": "robopay.sim2sim.v1",
        "profile_id": "limx.tron2.wheeled-mujoco-webots-obstacle-nav.v1",
        "success": all(checks.values()),
        "score": round(sum(checks.values()) / len(checks), 3),
        "checks": checks,
        "mujoco": mujoco_result,
        "webots": webots_result,
    }
    output = PROFILE_ROOT / "artifacts" / "sim2sim_result.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
