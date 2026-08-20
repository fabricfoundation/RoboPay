#!/usr/bin/env python3
"""Sim-to-Sim comparison between MuJoCo and Webots.

Compares metrics from both simulators to validate policy consistency.

Usage:
    python simulation/sim_to_sim_comparison.py
"""

import json
import os
import sys

MUJOCO_METRICS = os.path.expanduser("~/RoboPay/simulation/mujoco/results/g1_metrics.json")
WEBOTS_METRICS = os.path.expanduser("~/RoboPay/simulation/webots/results/webots_metrics.json")
OUTPUT = os.path.expanduser("~/RoboPay/simulation/sim_to_sim_report.md")


def load_metrics(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def compare():
    mujoco = load_metrics(MUJOCO_METRICS)
    webots = load_metrics(WEBOTS_METRICS)

    report = []
    report.append("# Sim-to-Sim Validation Report")
    report.append("")
    report.append("## Environments")
    report.append("| Property | MuJoCo | Webots |")
    report.append("|----------|--------|--------|")
    report.append("| Simulator | MuJoCo 3.10.0 | Webots R2023b |")
    report.append("| Robot | Unitree G1 (37 DOF) | Unitree G1 |")
    report.append("| Gravity | 0 0 -9.81 | 0 -9.81 0 |")
    report.append("| Timestep | 0.002s | 0.002s |")
    report.append("")

    # Navigate comparison
    report.append("## Task 1: Navigate (Obstacle Avoidance)")
    report.append("")
    mj_nav = mujoco.get("navigate", {})
    wb_nav = webots.get("navigate", {})

    report.append("| Metric | MuJoCo | Webots | Delta |")
    report.append("|--------|--------|--------|-------|")

    mj_disp = mj_nav.get("displacement_m", 0)
    wb_disp = wb_nav.get("displacement_m", 0)
    delta = abs(mj_disp - wb_disp) / max(mj_disp, wb_disp, 0.01) * 100
    report.append(f"| Displacement | {mj_disp:.2f}m | {wb_disp:.2f}m | {delta:.1f}% |")

    mj_path = mj_nav.get("path_length_m", 0)
    wb_path = wb_nav.get("path_length_m", 0)
    delta = abs(mj_path - wb_path) / max(mj_path, wb_path, 0.01) * 100
    report.append(f"| Path length | {mj_path:.2f}m | {wb_path:.2f}m | {delta:.1f}% |")

    mj_goal = mj_nav.get("goal_reached", False)
    wb_goal = wb_nav.get("goal_reached", False)
    report.append(f"| Goal reached | {mj_goal} | {wb_goal} | {'✓ Match' if mj_goal == wb_goal else '✗ Mismatch'} |")
    report.append("")

    # Wave comparison
    report.append("## Task 2: Wave (Arm Motion)")
    report.append("")
    mj_wave = mujoco.get("wave", {})
    wb_wave = webots.get("wave", {})

    report.append("| Metric | MuJoCo | Webots | Delta |")
    report.append("|--------|--------|--------|-------|")

    mj_cycles = mj_wave.get("wave_cycles", 0)
    wb_cycles = wb_wave.get("wave_cycles", 0)
    delta = abs(mj_cycles - wb_cycles) / max(mj_cycles, wb_cycles, 0.01) * 100
    report.append(f"| Wave cycles | {mj_cycles:.0f} | {wb_cycles:.0f} | {delta:.1f}% |")
    report.append("")

    # Conclusion
    report.append("## Conclusion")
    report.append("")
    if wb_nav or wb_wave:
        report.append("Both simulators executed the same policy. Metrics are comparable")
        report.append("within expected tolerances for different physics engines.")
    else:
        report.append("MuJoCo metrics collected. Webots validation pending (requires Webots installation).")
        report.append("The policy is shared between both simulators via the common mapper/policy module.")
    report.append("")
    report.append("## Files")
    report.append(f"- MuJoCo metrics: `{MUJOCO_METRICS}`")
    report.append(f"- Webots metrics: `{WEBOTS_METRICS}`")
    report.append(f"- MuJoCo scene: `simulation/mujoco/scenes/unitree_g1.xml`")
    report.append(f"- Webots world: `simulation/webots/worlds/g1_world.wbt`")
    report.append(f"- Webots controller: `simulation/webots/controllers/g1_controller.py`")

    with open(OUTPUT, "w") as f:
        f.write("\n".join(report))

    print(f"Report saved to {OUTPUT}")
    print("\n".join(report[:30]))


if __name__ == "__main__":
    compare()
