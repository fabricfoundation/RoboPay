"""Webots controller for Unitree G1 — Sim-to-Sim validation.

This controller runs the same policy as the MuJoCo runner, enabling
Sim-to-Sim validation between MuJoCo and Webots simulators.

The policy produces identical actions given the same inputs, so both
simulators should produce similar metrics (displacement, wave cycles, etc.).

Usage:
    1. Open Webots scene: simulation/webots/worlds/g1_world.wbt
    2. Run this controller
    3. Compare metrics with MuJoCo results
"""

import math
import json
import os

try:
    from controller import Robot, Motor, GPS
except ImportError:
    print("Webots controller module not available (running outside Webots)")
    print("This file is for Sim-to-Sim validation with Webots simulator.")
    print("Run inside Webots or use the MuJoCo runner instead.")
    exit(0)


def run_navigate(robot, timestep, duration_steps=3000):
    """Navigate to goal — same policy as MuJoCo runner."""
    gps = robot.getDevice("gps")
    gps.enable(timestep)

    # Get motor devices
    motors = []
    for name in ["left_arm_motor", "right_arm_motor", "left_leg_motor", "right_leg_motor"]:
        try:
            motors.append(robot.getDevice(name))
        except:
            pass

    goal_x, goal_y = 3.0, 0.0
    start_pos = None
    path_length = 0.0
    prev_pos = None

    for step in range(duration_steps):
        robot.step(timestep)

        pos = gps.getValues()
        if start_pos is None:
            start_pos = list(pos)
            prev_pos = list(pos)

        # Path length
        dx = pos[0] - prev_pos[0]
        dy = pos[1] - prev_pos[1]
        path_length += math.sqrt(dx**2 + dy**2)
        prev_pos = list(pos)

        # Navigate
        dx = goal_x - pos[0]
        dy = goal_y - pos[1]
        dist = math.sqrt(dx**2 + dy**2)

        if dist < 0.3:
            print(f"Webots: GOAL REACHED at step {step}! dist={dist:.3f}m")
            break

        speed = min(0.5, dist * 0.3)
        # Apply to motors (simplified)
        for motor in motors:
            motor.setPosition(float("inf"))
            motor.setVelocity(speed)

    # Metrics
    displacement = math.sqrt((pos[0] - start_pos[0])**2 + (pos[1] - start_pos[1])**2)
    return {
        "task": "navigate",
        "start_position": start_pos,
        "final_position": list(pos),
        "displacement_m": displacement,
        "path_length_m": path_length,
        "goal_reached": dist < 0.3,
        "simulator": "webots",
    }


def run_wave(robot, timestep, duration_steps=1500):
    """Wave arm — same policy as MuJoCo runner."""
    try:
        right_arm = robot.getDevice("right_arm_motor")
    except:
        print("No right_arm_motor found")
        return {"task": "wave", "error": "no motor"}

    wave_cycles = 0.0
    for step in range(duration_steps):
        robot.step(timestep)
        phase = step * 0.15
        angle = -1.5 + 0.8 * math.sin(phase)
        right_arm.setPosition(angle)
        wave_cycles = phase / (2 * math.pi)

    return {
        "task": "wave",
        "wave_cycles": wave_cycles,
        "duration_steps": duration_steps,
        "simulator": "webots",
    }


def main():
    robot = Robot()
    timestep = int(robot.getBasicTimeStep())

    print("=" * 50)
    print("Webots G1 Controller — Sim-to-Sim Validation")
    print("=" * 50)

    results = {}

    # Task 1: Navigate
    print("\nRunning navigate task...")
    results["navigate"] = run_navigate(robot, timestep)

    # Task 2: Wave
    print("\nRunning wave task...")
    results["wave"] = run_wave(robot, timestep)

    # Save results
    output = os.path.expanduser("~/RoboPay/simulation/webots/results/webots_metrics.json")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to {output}")

    # Compare with MuJoCo
    mujoco_path = os.path.expanduser("~/RoboPay/simulation/mujoco/results/g1_metrics.json")
    if os.path.exists(mujoco_path):
        with open(mujoco_path) as f:
            mujoco_data = json.load(f)
        print("\n=== Sim-to-Sim Comparison ===")
        if "navigate" in mujoco_data and "navigate" in results:
            mj_disp = mujoco_data["navigate"].get("displacement_m", 0)
            wb_disp = results["navigate"].get("displacement_m", 0)
            print(f"  Navigate displacement: MuJoCo={mj_disp:.2f}m, Webots={wb_disp:.2f}m")
        if "wave" in mujoco_data and "wave" in results:
            mj_cycles = mujoco_data["wave"].get("wave_cycles", 0)
            wb_cycles = results["wave"].get("wave_cycles", 0)
            print(f"  Wave cycles: MuJoCo={mj_cycles:.0f}, Webots={wb_cycles:.0f}")


if __name__ == "__main__":
    main()
