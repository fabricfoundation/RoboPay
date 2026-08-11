from limx_tron2_sim.contracts import NAVIGATION_SKILL, STOP_SKILL, NavigationRequest
from limx_tron2_sim.course import OBSTACLES, WAYPOINTS
from limx_tron2_sim.runtime import run_mujoco_episode


def test_official_model_and_policy_complete_measured_course() -> None:
    result = run_mujoco_episode(NavigationRequest(NAVIGATION_SKILL))
    assert result["success"] is True
    assert result["model_variant"] == "WF_TRON2A"
    assert result["low_level_controller"] == "limx-isaacgym-onnx-policy"
    assert result["waypoints_completed"] == result["waypoints_total"] == len(WAYPOINTS)
    assert len(result["detected_obstacles"]) == len(OBSTACLES)
    assert result["collision"] is False
    assert result["minimum_clearance_m"] > 0.05


def test_stop_does_not_start_navigation() -> None:
    result = run_mujoco_episode(NavigationRequest(STOP_SKILL))
    assert result == {
        "success": True,
        "skill": STOP_SKILL,
        "message": "safe stop acknowledged; zero velocity command retained",
        "simulator": "mujoco",
        "stopped": True,
    }
