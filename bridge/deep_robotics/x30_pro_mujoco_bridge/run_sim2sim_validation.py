"""Run the same bounded X30 lane request in MuJoCo and Webots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from x30_pro_mujoco_bridge.contracts import DRIVE_SKILL, validate_action
from x30_pro_mujoco_bridge.course import (
    BLOCKER_CENTERS_M,
    FIRST_CLEARANCE_OFFSET_M,
    FINISH_LINE_X_M,
    MAX_APPROACH_CLEARANCE_M,
    MIN_FORWARD_PROGRESS_M,
    SECOND_CLEARANCE_OFFSET_M,
    fingerprint as course_fingerprint,
    spec as course_spec,
)
from x30_pro_mujoco_bridge.runtime import run_drive_episode
from x30_pro_mujoco_bridge.webots import run_webots_episode


def _acceptance_checks(result: dict, *, simulator: str) -> dict[str, bool]:
    """Check the common course contract against an engine-owned result."""

    course = result.get("course", {})
    expected_centers = [list(center) for center in BLOCKER_CENTERS_M]
    phases = [item.get("phase") for item in result.get("controller_phase_transitions", [])]
    return {
        "engine_reported_success": result.get("success") is True,
        "correct_simulator": result.get("simulator_engine") == simulator,
        "canonical_course_id": result.get("course_id") == course_spec()["course_id"],
        "canonical_course_hash": result.get("course_hash") == course_fingerprint(),
        "canonical_blocker_geometry": course.get("blocker_centers_m") == expected_centers,
        "heading_faces_course": float(result.get("body_heading_course_alignment", -1.0)) >= 0.95,
        "body_forward_progress": float(result.get("body_forward_progress_m", 0.0)) >= MIN_FORWARD_PROGRESS_M,
        "first_side_evasion": max(
            float(result.get("max_positive_route_side_offset_m", 0.0)),
            abs(float(result.get("min_negative_route_side_offset_m", 0.0))),
        ) >= FIRST_CLEARANCE_OFFSET_M,
        "second_widened_side_evasion": max(
            float(result.get("max_positive_route_side_offset_m", 0.0)),
            abs(float(result.get("min_negative_route_side_offset_m", 0.0))),
        ) >= SECOND_CLEARANCE_OFFSET_M,
        "finish_line_crossed": result.get("finish_line_crossed") is True
        and float(course.get("finish_line_x_m", float("inf"))) == FINISH_LINE_X_M,
        "physical_course_approached": course.get("obstacle_approached") is True
        and float(course.get("minimum_approach_clearance_m", float("inf"))) <= MAX_APPROACH_CLEARANCE_M,
        "zero_blocker_contact": course.get("obstacle_collision_observed") is False,
        "measured_state_task_goal": result.get("task_goal_reached") is True
        and phases == ["settle", "evade_first", "pass_first", "evade_second", "pass_second", "goal_hold"],
        "finite_state": result.get("finite_state", True) is True,
        "safe_height": float(result.get("min_base_height_m", 0.0)) >= 0.30,
        "safe_tilt": float(result.get("max_tilt_rad", float("inf"))) <= 0.70,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--viewer",
        action="store_true",
        help="open the real MuJoCo and Webots desktop views instead of headless Webots",
    )
    args = parser.parse_args()
    request = validate_action(
        "x30-pro-sim-01",
        DRIVE_SKILL,
        DRIVE_SKILL,
        {},
    )
    mujoco_result = run_drive_episode(request, viewer=args.viewer)
    webots_result = run_webots_episode(request, viewer=args.viewer)
    mujoco_checks = _acceptance_checks(mujoco_result, simulator="MuJoCo")
    webots_checks = _acceptance_checks(webots_result, simulator="Webots")
    success = bool(all(mujoco_checks.values()) and all(webots_checks.values()))
    report = {
        "task": "x30_pro_inspection_lane_sim2sim",
        "status": "success" if success else "failure",
        "success": success,
        "shared_task_controller": {
            "controller_id": "x30-pro-two-obstacle-slow-slalom-v5",
            "route": "inspection-lane-v1",
            "gait_cycles": request.gait_cycles,
            "hip_sweep_rad": request.hip_sweep_rad,
            "max_duration_sec": request.max_duration_sec,
            "state_authority": "measured base pose drives phase transitions and terminal goal in each simulator",
        },
        "course_contract": {**course_spec(), "course_hash": course_fingerprint()},
        "acceptance_checks": {"mujoco": mujoco_checks, "webots": webots_checks},
        "mujoco": mujoco_result,
        "webots": webots_result,
        "note": "MuJoCo uses the locked vendor MJCF. Webots converts the same locked vendor URDF at runtime. A canonical course contract renders the same two physical blockers in both engines; each uses its own measured base/contact state to gate a slow lateral detour and forward pass around both blockers. The generated PROTO is not vendor supplied.",
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    artifact = Path(__file__).resolve().parent / "artifacts" / "sim2sim_result.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(rendered + "\n", encoding="utf-8")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
