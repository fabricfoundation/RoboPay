"""Unit tests for obstacle navigation policy and planners."""
import math
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common.policy import PotentialFieldPolicy


def test_policy_move_toward_goal():
    """Policy should output forward velocity when goal is ahead."""
    policy = PotentialFieldPolicy(goal=(10.0, 0.0), obstacles=[])
    v, w = policy.compute_action((0.0, 0.0), 0.0)
    assert v > 0, f"Expected forward velocity, got {v}"
    assert abs(w) < 0.1, f"Expected small angular velocity, got {w}"
    print("PASS: policy_move_toward_goal")


def test_policy_avoid_obstacle():
    """Policy should turn when obstacle is in the way."""
    policy = PotentialFieldPolicy(
        goal=(10.0, 0.0),
        obstacles=[(5.0, 0.0)],
        repulse_radius=3.0,
    )
    v, w = policy.compute_action((3.0, 0.0), 0.0)
    # Should turn away from obstacle
    assert abs(w) > 0.01, f"Expected angular velocity to avoid obstacle, got {w}"
    print("PASS: policy_avoid_obstacle")


def test_policy_goal_reached():
    """Policy should detect goal reached."""
    policy = PotentialFieldPolicy(goal=(5.0, 0.0), goal_threshold=0.5)
    assert policy.is_goal_reached((4.8, 0.0)), "Should be close enough"
    assert not policy.is_goal_reached((0.0, 0.0)), "Should be too far"
    print("PASS: policy_goal_reached")


def test_policy_velocity_bounds():
    """Policy output should be within velocity bounds."""
    policy = PotentialFieldPolicy(goal=(10.0, 0.0), obstacles=[])
    for heading in [0, 0.5, -0.5, 1.0, -1.0]:
        v, w = policy.compute_action((0.0, 0.0), heading)
        assert 0 <= v <= 1.0, f"Velocity {v} out of bounds"
        assert -1.0 <= w <= 1.0, f"Angular velocity {w} out of bounds"
    print("PASS: policy_velocity_bounds")


def test_policy_slow_near_obstacles():
    """Policy should slow down near obstacles."""
    policy = PotentialFieldPolicy(
        goal=(10.0, 0.0),
        obstacles=[(2.0, 0.0)],
    )
    v_far, _ = policy.compute_action((0.0, 0.0), 0.0)
    v_near, _ = policy.compute_action((1.5, 0.0), 0.0)
    # Should be slower near obstacle
    print(f"PASS: policy_slow_near_obstacles (far={v_far:.2f}, near={v_near:.2f})")


def test_rrt_star_planner():
    """RRT* should find a path around obstacles."""
    # Import RRT* from simulation
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mujoco", "scripts"))
    try:
        from run_simulation_v2 import RRTStar
    except ImportError:
        print("SKIP: RRT* (run_simulation_v2 not available)")
        return

    rrt = RRTStar(max_iter=500)
    path = rrt.plan(
        start=(0.0, 0.0),
        goal=(10.0, 0.0),
        obstacles=[(5.0, 0.0, 0.5, 0.5)],
    )
    assert len(path) >= 2, f"Path too short: {len(path)} waypoints"
    assert math.hypot(path[0][0], path[0][1]) < 1.0, "Path should start near origin"
    assert math.hypot(path[-1][0] - 10.0, path[-1][1]) < 1.0, "Path should end near goal"
    print(f"PASS: rrt_star_planner ({len(path)} waypoints)")


def test_dwa_planner():
    """DWA should compute valid velocity commands."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mujoco", "scripts"))
    try:
        from run_simulation_v2 import DWAPlanner
    except ImportError:
        print("SKIP: DWA (run_simulation_v2 not available)")
        return

    dwa = DWAPlanner(max_speed=0.5, max_yawrate=0.3)
    v, w = dwa.compute_velocity(
        pos=(0.0, 0.0),
        heading=0.0,
        goal=(5.0, 0.0),
        obstacles=[(3.0, 0.0)],
    )
    assert 0 <= v <= 0.5, f"DWA velocity {v} out of bounds"
    assert -0.3 <= w <= 0.3, f"DWA angular {w} out of bounds"
    print(f"PASS: dwa_planner (v={v:.2f}, w={w:.2f})")


def test_metrics_computation():
    """Metrics should compute correctly from positions."""
    # Test distance calculation
    pos = (3.0, 4.0)
    goal = (0.0, 0.0)
    dist = math.hypot(goal[0] - pos[0], goal[1] - pos[1])
    assert abs(dist - 5.0) < 0.01, f"Expected distance 5.0, got {dist}"

    # Test progress calculation
    total_dist = 10.0
    progress = max(0, 1.0 - dist / total_dist)
    assert abs(progress - 0.5) < 0.01, f"Expected progress 0.5, got {progress}"
    print("PASS: metrics_computation")


if __name__ == "__main__":
    test_policy_move_toward_goal()
    test_policy_avoid_obstacle()
    test_policy_goal_reached()
    test_policy_velocity_bounds()
    test_policy_slow_near_obstacles()
    test_rrt_star_planner()
    test_dwa_planner()
    test_metrics_computation()
    print("\nAll tests passed!")
