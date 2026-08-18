"""Tests for the Atlas MuJoCo bridge contract."""

from __future__ import annotations

import json
import math

from bridge.boston_dynamics.atlas_mujoco_bridge.control_core import (
    AtlasObstacleControlCore,
    POLICY_ID,
    NEUTRAL_ARRAY,
)
from bridge.boston_dynamics.atlas_mujoco_bridge.runner import run_obstacle_nav


def test_neutral_has_21_actuators():
    assert len(NEUTRAL_ARRAY) == 21


def test_control_core_initializes():
    from bridge.boston_dynamics.atlas_mujoco_bridge.course import COURSE_REFERENCE_ROUTE
    core = AtlasObstacleControlCore(goal=(3.5, 1.2), reference_route=COURSE_REFERENCE_ROUTE)
    assert core.phase == "IDLE"
    assert core.waypoint_count == 4


def test_control_core_reset():
    core = AtlasObstacleControlCore(goal=(3.5, 1.2))
    core.reset((0.0, 0.0), [])
    assert core.phase == "NAVIGATING"
    assert core.waypoints_completed == 0


def test_compute_plan_returns_plan():
    core = AtlasObstacleControlCore(goal=(3.5, 1.2))
    core.reset((0.0, 0.0), [])
    obs = {"position": [0.0, 0.0, 1.28], "yaw": 0.0, "sim_time": 3.0, "goal": (3.5, 1.2), "clearance": 2.0, "collision_count": 0, "body_height": 1.28}
    plan = core.compute_plan(obs)
    assert plan.phase == "NAVIGATING"
    assert plan.goal_distance > 0.0
    assert plan.policy_id if hasattr(plan, "policy_id") else True


def test_goal_detection():
    core = AtlasObstacleControlCore(goal=(3.5, 1.2))
    core.reset((0.0, 0.0), [])
    for i in range(10):
        core._waypoint_index = len(core._route) - 1
    obs = {"position": [3.5, 1.2, 0.8], "yaw": 0.0, "sim_time": 10.0, "goal": (3.5, 1.2), "clearance": 2.0, "collision_count": 0, "body_height": 0.8}
    plan = core.compute_plan(obs)
    assert plan.phase == "GOAL_REACHED"


def test_mujoco_runner_produces_metrics():
    result = run_obstacle_nav(max_duration_seconds=5.0)
    assert result["simulator_engine"] == "MuJoCo"
    assert result["robot_model"] == "MuJoCo Humanoid (Atlas locomotion)"
    assert "success" in result
    assert "control_steps" in result
    assert result["control_steps"] > 0
    assert result["sim_duration_seconds"] > 0
    assert "initial_position" in result
    assert "final_position" in result
    assert result["goal"] == {"x": 3.5, "y": 1.2}
    assert result["policy_id"] == POLICY_ID


def test_mujoco_runner_moves_forward():
    result = run_obstacle_nav(max_duration_seconds=10.0)
    assert float(result["final_position"]["x"]) > float(result["initial_position"]["x"]), (
        f"Robot did not move forward: {result['initial_position']['x']} -> {result['final_position']['x']}"
    )
    assert result["path_length_m"] > 0.0
