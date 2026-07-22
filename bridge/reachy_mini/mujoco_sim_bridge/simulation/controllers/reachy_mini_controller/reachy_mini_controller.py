"""Webots controller for Reachy Mini — cycles through apple, croissant, duck."""
import sys
import os
import json
import math
import numpy as np

_CONTROLLER_DIR = os.path.dirname(os.path.abspath(__file__))
# _BRIDGE_ROOT points to mujoco_sim_bridge root directory so 'from policy...' and 'from simulation...' imports work
_BRIDGE_ROOT = os.path.normpath(os.path.join(_CONTROLLER_DIR, "..", "..", ".."))
if _BRIDGE_ROOT not in sys.path:
    sys.path.insert(0, _BRIDGE_ROOT)

OUTPUT_FILE = os.path.normpath(os.path.join(_CONTROLLER_DIR, "..", "..", "webots_sim2sim_result.json"))

# Try to find Webots controller python lib from WEBOTS_HOME env var if set
webots_home = os.environ.get("WEBOTS_HOME")
if webots_home:
    controller_py = os.path.join(webots_home, "lib", "controller", "python")
    if os.path.exists(controller_py) and controller_py not in sys.path:
        sys.path.insert(0, controller_py)

TARGETS = ["apple", "croissant", "duck"]
MAX_PER_TARGET = 4.0

from controller import Supervisor
from policy.controller import ReachyTaskPolicy
from simulation.metrics import SimulationMetricsTracker

OBJECT_POSITIONS = {
    "apple":     np.array([0.6, -0.2, 0.07]),
    "croissant": np.array([0.6,  0.1, 0.065]),
    "duck":      np.array([0.6,  0.3, 0.065]),
}


def get_obs(robot, yaw_sensor, target_pos, target_sim_time):
    yaw = yaw_sensor.getValue()
    head_pos = np.array([0.0, 0.0, 0.36])
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    xmat = np.array([
        [cos_y, -sin_y, 0.0],
        [sin_y,  cos_y, 0.0],
        [  0.0,    0.0, 1.0],
    ]).flatten()
    return {
        "sim_time":      target_sim_time,
        "head_pos":      head_pos,
        "head_xmat":     xmat,
        "base_yaw":      yaw,
        "target_pos":    target_pos,
        "apple_pos":     OBJECT_POSITIONS["apple"],
        "croissant_pos": OBJECT_POSITIONS["croissant"],
        "duck_pos":      OBJECT_POSITIONS["duck"],
        "stewart_qpos":  np.zeros(6),
        "antenna_qpos":  np.zeros(2),
        "num_contacts":  0,
    }


def main():
    robot     = Supervisor()
    timestep  = int(robot.getBasicTimeStep())

    yaw_motor = robot.getDevice("yaw_body")
    yaw_sensor = robot.getDevice("yaw_body_sensor")
    yaw_sensor.enable(timestep)

    all_results   = []
    phase_history = []

    for target_name in TARGETS:
        policy  = ReachyTaskPolicy()
        metrics = SimulationMetricsTracker()
        policy.reset()
        target_pos   = OBJECT_POSITIONS[target_name]
        t0           = robot.getTime()
        last_summary = {}

        obs = get_obs(robot, yaw_sensor, target_pos, 0.0)
        metrics.reset(obs)
        print(f"[WebotsController] Starting track -> {target_name.upper()}")

        while robot.step(timestep) != -1:
            elapsed = robot.getTime() - t0
            obs     = get_obs(robot, yaw_sensor, target_pos, elapsed)

            action, phase = policy.compute_action(obs, last_summary)
            phase_history.append(phase)
            last_summary = metrics.update(obs)

            # Direct motor position control
            target_yaw = float(action[0])
            yaw_motor.setPosition(target_yaw)

            if elapsed >= MAX_PER_TARGET:
                break

        s = metrics.get_summary()
        all_results.append({
            "target_object":         target_name,
            "task_completed":        s["task_completed"],
            "tracking_success_rate": round(float(s["tracking_success_rate"]), 4),
            "min_tracking_error_rad":round(float(s["min_tracking_error_rad"]), 4),
            "success_rate_score":    round(float(s["success_rate_score"]), 4),
        })

    avg_score = float(np.mean([r["success_rate_score"] for r in all_results]))
    result = {
        "simulator_engine":       "Webots",
        "targets_tracked":        TARGETS,
        "phases_visited":         sorted(set(phase_history)),
        "sim_duration_seconds":   round(robot.getTime(), 2),
        "task_completed":         all(r["task_completed"] for r in all_results),
        "tracking_success_rate":  round(avg_score, 4),
        "success_rate_score":     round(avg_score, 4),
        "per_target":             all_results,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    if os.environ.get("WEBOTS_INTERACTIVE") == "1":
        print("[WebotsController] Interactive mode: holding the scene open.")
        while robot.step(timestep) != -1:
            pass
    else:
        robot.simulationQuit(0)


if __name__ == "__main__":
    main()
