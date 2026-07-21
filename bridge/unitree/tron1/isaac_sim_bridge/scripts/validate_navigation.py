#!/usr/bin/env python3
"""Sim-to-Sim validation for TRON1 obstacle navigation skill.

Runs the obstacle navigation policy against test scenarios WITHOUT
requiring Isaac Sim. Validates that the policy:
  1. Reaches goals (or gets close)
  2. Avoids obstacles
  3. Reports accurate metrics
  4. Completes within step limits

TRON1 humanoid constraints:
  vx: [0, 0.8] m/s   wz: [-0.3, 0.3] rad/s
  Forward-only locomotion (no backward walking)

Usage:
    python3 validate_navigation.py [--scenario SCENARIO_NAME]
"""
import argparse
import math
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tron1.skills.obstacle_nav import ObstacleNavSkill


def simulate_robot_navigation(skill: ObstacleNavSkill, scenario: dict) -> dict:
    """Simulate robot navigation using the policy.

    This is a simplified 2D simulation that:
    1. Runs the policy to get velocity commands
    2. Integrates velocity to update position
    3. Checks for collisions with obstacles
    4. Returns final metrics
    """
    start = tuple(scenario["start"])
    goal = tuple(scenario["goal"])
    obstacles = [tuple(obs) for obs in scenario.get("obstacles", [])]

    # Initialize
    skill.reset({
        "goal": goal,
        "start": start,
        "obstacles": obstacles,
    })

    pos = list(start)
    heading = math.atan2(goal[1] - start[1], goal[0] - start[0])
    dt = 0.1  # 100ms timestep

    collision_count = 0
    min_obstacle_dist = float("inf")

    for step in range(1500):
        # Check collisions
        collision = False
        for obs in obstacles:
            dist = math.hypot(pos[0] - obs[0], pos[1] - obs[1])
            min_obstacle_dist = min(min_obstacle_dist, dist)
            if dist < 0.3:  # Collision radius
                collision = True
                collision_count += 1
                # Push robot away from obstacle
                dx = pos[0] - obs[0]
                dy = pos[1] - obs[1]
                push_dist = math.hypot(dx, dy)
                if push_dist > 0:
                    pos[0] += 0.5 * dx / push_dist
                    pos[1] += 0.5 * dy / push_dist

        # Get policy action
        state = {
            "position": tuple(pos),
            "heading": heading,
            "collision": collision,
        }
        result = skill.step(state)

        if result.status == "success":
            break

        # Integrate velocity
        if result.action == "move_forward":
            pos[0] += result.speed * math.cos(heading) * dt
            pos[1] += result.speed * math.sin(heading) * dt
            heading += result.angular_speed * dt
        elif result.action == "turn_left":
            heading += 0.3 * dt  # TRON1 max angular
            pos[0] += 0.15 * math.cos(heading) * dt
            pos[1] += 0.15 * math.sin(heading) * dt
        elif result.action == "turn_right":
            heading -= 0.3 * dt  # TRON1 max angular
            pos[0] += 0.15 * math.cos(heading) * dt
            pos[1] += 0.15 * math.sin(heading) * dt

    # Final metrics
    final_dist = math.hypot(pos[0] - goal[0], pos[1] - goal[1])
    total_dist = math.hypot(goal[0] - start[0], goal[1] - start[1])
    progress = max(0, 1.0 - final_dist / total_dist) if total_dist > 0 else 1.0

    return {
        "final_position": {"x": round(pos[0], 3), "y": round(pos[1], 3)},
        "distance_to_goal": round(final_dist, 3),
        "path_progress": round(progress, 3),
        "collision_count": collision_count,
        "min_obstacle_clearance": round(min_obstacle_dist, 3) if min_obstacle_dist != float("inf") else None,
        "steps": skill._step_count,
        "status": skill._status,
    }


def validate_scenario(name: str, scenario: dict, verbose: bool = False) -> bool:
    """Validate a single scenario."""
    print(f"\n{'='*60}")
    print(f"Scenario: {name}")
    print(f"  Description: {scenario['description']}")
    print(f"  Start: {scenario['start']}")
    print(f"  Goal: {scenario['goal']}")
    print(f"  Obstacles: {scenario.get('obstacles', [])}")

    skill = ObstacleNavSkill(
        goal_threshold=0.3,
        max_speed=0.8,  # TRON1 max
    )
    result = simulate_robot_navigation(skill, scenario)

    expected = scenario["expected"]
    passed = True

    # Check collision count
    if result["collision_count"] > expected["max_collisions"]:
        print(f"  FAIL: {result['collision_count']} collisions > max {expected['max_collisions']}")
        passed = False
    else:
        print(f"  PASS: {result['collision_count']} collisions <= {expected['max_collisions']}")

    # Check progress
    if result["path_progress"] < expected["min_progress"]:
        print(f"  FAIL: progress {result['path_progress']} < min {expected['min_progress']}")
        passed = False
    else:
        print(f"  PASS: progress {result['path_progress']} >= {expected['min_progress']}")

    # Check step count
    if result["steps"] > expected["max_steps"]:
        print(f"  FAIL: {result['steps']} steps > max {expected['max_steps']}")
        passed = False
    else:
        print(f"  PASS: {result['steps']} steps <= {expected['max_steps']}")

    if verbose:
        print(f"\n  Results:")
        for key, val in result.items():
            print(f"    {key}: {val}")

    return passed


def main():
    parser = argparse.ArgumentParser(description="Validate TRON1 obstacle navigation")
    parser.add_argument("--scenario", help="Run specific scenario")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    # Load scenarios
    import yaml
    scenario_file = os.path.join(
        os.path.dirname(__file__), "..", "config", "scenarios", "nav_scenarios.yaml"
    )
    with open(scenario_file) as f:
        data = yaml.safe_load(f)

    scenarios = data["scenarios"]
    if args.scenario:
        if args.scenario not in scenarios:
            print(f"Unknown scenario: {args.scenario}")
            print(f"Available: {list(scenarios.keys())}")
            sys.exit(1)
        scenarios = {args.scenario: scenarios[args.scenario]}

    # Run validation
    results = {}
    for name, scenario in scenarios.items():
        results[name] = validate_scenario(name, scenario, args.verbose)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    all_passed = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\nAll scenarios PASSED - Sim-to-Sim validation successful!")
        sys.exit(0)
    else:
        print("\nSome scenarios FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
