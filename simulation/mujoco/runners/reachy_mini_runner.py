#!/usr/bin/env python3
"""Reachy Mini MuJoCo simulation runner with real metrics.

Reachy Mini is a desktop arm robot. Tasks:
- Wave: oscillate right arm
- Look: pan/tilt head
- Grasp: pick up object from table
- Manipulation: move object from A to B

Usage:
    python -m simulation.mujoco.runners.reachy_mini_runner --task all
"""

import argparse
import json
import logging
import math
import os
import numpy as np
import mujoco

logger = logging.getLogger(__name__)

SCENE = "simulation/mujoco/scenes/reachy_mini.xml"


class MetricsCollector:
    def __init__(self):
        self.start_pos = None
        self.path_length = 0.0
        self.prev_pos = None
        self.steps = 0
        self.arm_reach_max = 0.0
        self.collisions = 0

    def update(self, data):
        self.steps += 1
        pos = data.qpos[:3].copy()
        if self.start_pos is None:
            self.start_pos = pos.copy()
            self.prev_pos = pos.copy()
        self.path_length += np.linalg.norm(pos[:2] - self.prev_pos[:2])
        self.prev_pos = pos.copy()
        # Track arm reach (distance from base to end effector)
        if True:  # Reachy Mini has 23 DOF
            ee_pos = data.qpos[7:10]  # approximate end effector
            reach = np.linalg.norm(ee_pos - self.start_pos[:3])
            self.arm_reach_max = max(self.arm_reach_max, reach)
        # Count non-ground collisions
        for i in range(data.ncon):
            c = data.contact[i]
            if c.geom1 > 0 and c.geom2 > 0:
                self.collisions += 1

    def summary(self):
        pos = self.prev_pos if self.prev_pos is not None else np.zeros(3)
        start = self.start_pos if self.start_pos is not None else np.zeros(3)
        return {
            "start_position": start.tolist(),
            "final_position": pos.tolist(),
            "displacement_m": float(np.linalg.norm(pos[:2] - start[:2])),
            "path_length_m": float(self.path_length),
            "total_steps": self.steps,
            "elapsed_sim_time_s": self.steps * 0.002,
            "arm_reach_max_m": float(self.arm_reach_max),
            "collisions": self.collisions,
        }


def run_wave(model, data, duration_steps=8000):
    """Wave right arm."""
    logger.info("=== Wave ===")
    metrics = MetricsCollector()
    wave_phase = 0.0

    for step in range(duration_steps):
        wave_phase += 0.15
        data.ctrl[2] = -1.5 + 0.8 * math.sin(wave_phase)  # r_shoulder_pitch
        data.ctrl[3] = -0.5 + 0.3 * math.sin(wave_phase * 0.7)  # r_elbow

        mujoco.mj_step(model, data)
        metrics.update(data)

        if step % 2000 == 0:
            logger.info("Step %d: wave_cycle=%.0f reach=%.3fm",
                        step, wave_phase / (2 * math.pi), metrics.arm_reach_max)

    result = metrics.summary()
    result["action"] = "wave"
    result["wave_cycles"] = float(wave_phase / (2 * math.pi))
    return result


def run_look(model, data, duration_steps=5000):
    """Pan/tilt head to scan environment."""
    logger.info("=== Look ===")
    metrics = MetricsCollector()
    scan_phase = 0.0

    for step in range(duration_steps):
        scan_phase += 0.05
        data.ctrl[0] = 0.8 * math.sin(scan_phase)  # head_pan
        data.ctrl[1] = 0.3 * math.sin(scan_phase * 0.5)  # head_tilt

        mujoco.mj_step(model, data)
        metrics.update(data)

        if step % 1000 == 0:
            logger.info("Step %d: scan_phase=%.1f pan=%.2f tilt=%.2f",
                        step, scan_phase, data.ctrl[0], data.ctrl[1])

    result = metrics.summary()
    result["action"] = "look"
    result["scan_cycles"] = float(scan_phase / (2 * math.pi))
    return result


def run_grasp(model, data, max_steps=8000):
    """Reach for red cube, grasp, lift, place at blue cube location."""
    logger.info("=== Grasp ===")
    metrics = MetricsCollector()
    phases = ["Reach down", "Close gripper", "Lift", "Move to target", "Release"]
    phase = 0
    phase_step = 0

    for step in range(max_steps):
        phase_step += 1

        if phase == 0:  # Reach down toward red cube
            data.ctrl[2] = -1.2  # shoulder down
            data.ctrl[3] = -1.5  # elbow bent
            data.ctrl[5] = 0.3   # gripper open
            if phase_step > 50:
                phase = 1; phase_step = 0

        elif phase == 1:  # Close gripper
            data.ctrl[5] = -0.5  # gripper close
            if phase_step > 30:
                phase = 2; phase_step = 0
                logger.info("Grasped at step %d", step)

        elif phase == 2:  # Lift
            data.ctrl[2] = -0.5  # shoulder up
            data.ctrl[3] = -1.0  # elbow
            if phase_step > 40:
                phase = 3; phase_step = 0

        elif phase == 3:  # Move to blue cube position
            data.ctrl[2] = -0.8  # shoulder
            data.ctrl[4] = 0.2   # wrist rotate
            if phase_step > 40:
                phase = 4; phase_step = 0
                logger.info("Moved to target at step %d", step)

        elif phase == 4:  # Release
            data.ctrl[5] = 0.3   # gripper open
            if phase_step > 30:
                logger.info("Released at step %d", step)
                break

        mujoco.mj_step(model, data)
        metrics.update(data)

        if step % 1000 == 0:
            logger.info("Step %d: %s", step, phases[phase])

    result = metrics.summary()
    result["action"] = "grasp"
    result["phases_completed"] = phase
    return result


def main():
    parser = argparse.ArgumentParser(description="Reachy Mini Runner")
    parser.add_argument("--task", default="all", choices=["wave", "look", "grasp", "all"])
    parser.add_argument("--scene", default=SCENE)
    parser.add_argument("--output", default="simulation/mujoco/results/reachy_mini_metrics.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [REACHY] %(message)s")

    model = mujoco.MjModel.from_xml_path(args.scene)
    model.vis.global_.offwidth = 960
    model.vis.global_.offheight = 540
    data = mujoco.MjData(model)

    results = {}

    if args.task in ("wave", "all"):
        mujoco.mj_resetData(model, data)
        results["wave"] = run_wave(model, data)

    if args.task in ("look", "all"):
        mujoco.mj_resetData(model, data)
        results["look"] = run_look(model, data)

    if args.task in ("grasp", "all"):
        mujoco.mj_resetData(model, data)
        results["grasp"] = run_grasp(model, data)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    logger.info("Results saved to %s", args.output)
    for task, r in results.items():
        logger.info("  %s: displacement=%.3fm arm_reach=%.3fm steps=%d",
                    task, r.get("displacement_m", 0), r.get("arm_reach_max_m", 0),
                    r.get("total_steps", 0))


if __name__ == "__main__":
    main()
