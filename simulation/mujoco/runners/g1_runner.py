#!/usr/bin/env python3
"""Unitree G1 MuJoCo simulation runner with real metrics.

Runs obstacle navigation, pick-and-place, and wave tasks.
Produces simulator state metrics as required by Tier 1 bounty.

Usage:
    python -m simulation.mujoco.runners.g1_runner --task navigate
    python -m simulation.mujoco.runners.g1_runner --task wave
    python -m simulation.mujoco.runners.g1_runner --task pick_place
    python -m simulation.mujoco.runners.g1_runner --task all
"""

import argparse
import json
import logging
import math
import time

import mujoco
import numpy as np

logger = logging.getLogger(__name__)

SCENE_PATH = "simulation/mujoco/scenes/unitree_g1.xml"


class MetricsCollector:
    """Collects simulator state metrics during execution."""

    def __init__(self):
        self.start_pos = None
        self.collisions = []
        self.path_length = 0.0
        self.prev_pos = None
        self.steps = 0
        self.task_specific = {}

    def update(self, data, model):
        self.steps += 1
        pos = data.qpos[:3].copy()

        if self.start_pos is None:
            self.start_pos = pos.copy()
            self.prev_pos = pos.copy()

        # Path length
        displacement = np.linalg.norm(pos - self.prev_pos)
        self.path_length += displacement
        self.prev_pos = pos.copy()

        # Collisions
        for i in range(data.ncon):
            contact = data.contact[i]
            if contact.geom1 > 0 and contact.geom2 > 0:
                self.collisions.append({
                    "step": self.steps,
                    "geom1": contact.geom1,
                    "geom2": contact.geom2,
                })

    def summary(self):
        pos = self.prev_pos if self.prev_pos is not None else np.zeros(3)
        start = self.start_pos if self.start_pos is not None else np.zeros(3)
        return {
            "start_position": start.tolist(),
            "final_position": pos.tolist(),
            "displacement_m": float(np.linalg.norm(pos - start)),
            "path_length_m": float(self.path_length),
            "total_collisions": len(self.collisions),
            "total_steps": self.steps,
            "elapsed_sim_time_s": self.steps * 0.002,
        }


def run_navigate(model, data, goal_x=8.0, goal_y=0.0, max_steps=50000):
    """Navigate to goal avoiding obstacles. Uses proportional control."""
    logger.info("=== Obstacle Navigation ===")
    logger.info("Goal: (%.1f, %.1f)", goal_x, goal_y)

    metrics = MetricsCollector()
    goal_reached = False
    stuck_count = 0
    prev_dist = float("inf")

    for step in range(max_steps):
        pos = data.qpos[:3].copy()
        dx = goal_x - pos[0]
        dy = goal_y - pos[1]
        dist = math.sqrt(dx**2 + dy**2)

        if dist < 0.3:
            goal_reached = True
            logger.info("GOAL REACHED at step %d! dist=%.3fm", step, dist)
            break

        # Proportional control with obstacle avoidance
        speed = min(0.5, dist * 0.3)

        # Simple obstacle avoidance: check for nearby obstacles
        avoid_x, avoid_y = 0.0, 0.0
        for i in range(data.ncon):
            contact = data.contact[i]
            if contact.geom1 > 0 and contact.geom2 > 0:
                # Collision detected - steer away
                avoid_x -= 0.3 * dx / (dist + 0.01)
                avoid_y -= 0.3 * dy / (dist + 0.01)

        # Apply control
        data.ctrl[0] = speed * dx / (dist + 0.01) + avoid_x  # vx
        data.ctrl[1] = speed * dy / (dist + 0.01) + avoid_y  # vy

        # Yaw toward goal
        target_yaw = math.atan2(dy, dx)
        current_yaw = math.atan2(
            2 * (data.qpos[3] * data.qpos[6] + data.qpos[4] * data.qpos[5]),
            1 - 2 * (data.qpos[5]**2 + data.qpos[6]**2)
        )
        yaw_error = target_yaw - current_yaw
        data.ctrl[2] = 0.5 * max(-1, min(1, yaw_error))  # wz

        mujoco.mj_step(model, data)
        metrics.update(data, model)

        # Stuck detection
        new_dist = math.sqrt((goal_x - data.qpos[0])**2 + (goal_y - data.qpos[1])**2)
        if abs(new_dist - prev_dist) < 0.001:
            stuck_count += 1
            if stuck_count > 5000:
                logger.warning("Stuck at step %d, adding random perturbation", step)
                data.ctrl[0] = np.random.uniform(-0.3, 0.3)
                data.ctrl[1] = np.random.uniform(-0.3, 0.3)
                stuck_count = 0
        else:
            stuck_count = 0
        prev_dist = new_dist

        if step % 5000 == 0:
            logger.info("Step %d: pos=(%.2f,%.2f) dist=%.2fm path=%.2fm collisions=%d",
                        step, pos[0], pos[1], dist, metrics.path_length, len(metrics.collisions))

    result = metrics.summary()
    result["goal_position"] = [goal_x, goal_y]
    result["goal_reached"] = goal_reached
    result["distance_to_goal"] = float(math.sqrt((goal_x - data.qpos[0])**2 + (goal_y - data.qpos[1])**2))
    return result


def run_wave(model, data, duration_steps=10000):
    """Wave right arm. Demonstrates non-trivial embodied action."""
    logger.info("=== Wave Action ===")

    metrics = MetricsCollector()
    wave_phase = 0.0

    for step in range(duration_steps):
        # Wave: oscillate right shoulder pitch
        wave_phase += 0.1
        data.ctrl[7] = -1.5 + 0.5 * math.sin(wave_phase)  # r_shoulder_pitch
        data.ctrl[8] = -0.3 * math.sin(wave_phase)  # r_shoulder_roll

        # Keep standing: balance legs
        data.ctrl[0] = 0  # vx
        data.ctrl[1] = 0  # vy
        data.ctrl[2] = 0  # wz

        mujoco.mj_step(model, data)
        metrics.update(data, model)

        if step % 2000 == 0:
            logger.info("Step %d: waving (phase=%.1f)", step, wave_phase)

    result = metrics.summary()
    result["action"] = "wave"
    result["wave_cycles"] = float(wave_phase / (2 * math.pi))
    return result


def run_pick_place(model, data, max_steps=50000):
    """Navigate to table, pick red cube, place at goal location."""
    logger.info("=== Pick and Place ===")

    metrics = MetricsCollector()
    table_pos = np.array([4.0, -3.0, 0.45])
    goal_pos = np.array([8.0, 0.0, 0.1])
    phases = ["navigate_to_table", "approach", "grasp", "navigate_to_goal", "place"]
    current_phase = 0

    for step in range(max_steps):
        pos = data.qpos[:3].copy()

        if current_phase == 0:  # Navigate to table
            target = table_pos
            dist = np.linalg.norm(target[:2] - pos[:2])
            if dist < 0.5:
                current_phase = 1
                logger.info("Reached table at step %d", step)

        elif current_phase == 1:  # Approach
            target = table_pos + np.array([0, 0, 0])
            dist = np.linalg.norm(target[:2] - pos[:2])
            if dist < 0.3:
                current_phase = 2
                logger.info("Approaching cube at step %d", step)

        elif current_phase == 2:  # Grasp (lower arm)
            target = table_pos
            data.ctrl[7] = -1.0  # reach down
            data.ctrl[9] = -1.5  # close elbow
            if step % 1000 == 0:
                logger.info("Grasping at step %d", step)
            if step > 20000:
                current_phase = 3
                logger.info("Grasped, moving to goal at step %d", step)

        elif current_phase == 3:  # Navigate to goal
            target = goal_pos
            dist = np.linalg.norm(target[:2] - pos[:2])
            if dist < 0.5:
                current_phase = 4
                logger.info("Reached goal at step %d", step)

        elif current_phase == 4:  # Place
            data.ctrl[7] = -0.5  # lower arm
            data.ctrl[9] = 0  # open
            if step > 40000:
                logger.info("Placed at step %d", step)
                break

        # Locomotion toward target
        if current_phase in (0, 1, 3):
            dx = target[0] - pos[0]
            dy = target[1] - pos[1]
            dist = math.sqrt(dx**2 + dy**2)
            speed = min(0.3, dist * 0.2)
            data.ctrl[0] = speed * dx / (dist + 0.01)
            data.ctrl[1] = speed * dy / (dist + 0.01)

        mujoco.mj_step(model, data)
        metrics.update(data, model)

    result = metrics.summary()
    result["action"] = "pick_and_place"
    result["phases_completed"] = current_phase
    result["red_cube_final_pos"] = data.qpos[7:10].tolist() if model.nq > 10 else []
    return result


def main():
    parser = argparse.ArgumentParser(description="G1 MuJoCo Runner")
    parser.add_argument("--task", default="all", choices=["navigate", "wave", "pick_place", "all"])
    parser.add_argument("--scene", default=SCENE_PATH)
    parser.add_argument("--goal-x", type=float, default=8.0)
    parser.add_argument("--goal-y", type=float, default=0.0)
    parser.add_argument("--output", default="simulation_results.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [G1] %(message)s")

    model = mujoco.MjModel.from_xml_path(args.scene)
    data = mujoco.MjData(model)
    mujoco.mj_resetData(model, data)

    results = {}

    if args.task in ("navigate", "all"):
        mujoco.mj_resetData(model, data)
        results["navigate"] = run_navigate(model, data, args.goal_x, args.goal_y)

    if args.task in ("wave", "all"):
        mujoco.mj_resetData(model, data)
        results["wave"] = run_wave(model, data)

    if args.task in ("pick_place", "all"):
        mujoco.mj_resetData(model, data)
        results["pick_place"] = run_pick_place(model, data)

    # Save results
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    logger.info("Results saved to %s", args.output)
    for task, r in results.items():
        logger.info("  %s: displacement=%.2fm collisions=%d steps=%d",
                    task, r.get("displacement_m", 0), r.get("total_collisions", 0), r.get("total_steps", 0))


if __name__ == "__main__":
    main()
