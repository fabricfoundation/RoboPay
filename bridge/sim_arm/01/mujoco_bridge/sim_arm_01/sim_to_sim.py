"""Sim-to-sim validation: run the same move_to_pose skill through MuJoCo and
PyBullet and confirm both engines converge to the same joint configuration.

    python -m sim_arm_01.sim_to_sim

The skill is defined in joint space, so the asserted invariant is that both
engines reach the target (same success verdict) to the same configuration
(max joint disagreement < JOINT_TOL) with the same collision verdict.
"""
import numpy as np

from .simulator import SimArm01Simulator
from .pybullet_simulator import SimArm01PyBullet

TARGETS = [
    [1.0, -0.5],
    [0.5, 0.5],
    [-1.2, 0.8],
    [2.0, -1.5],
]
JOINT_TOL = 0.05   # rad — max allowed disagreement between engines


def main():
    print(f"{'target':>18} | {'mujoco err':>10} | {'pybullet err':>12} | "
          f"{'joint diff':>10} | consistent")
    print("-" * 74)

    all_ok = True
    for target in TARGETS:
        mj = SimArm01Simulator().execute(target)
        pb_env = SimArm01PyBullet()
        pb = pb_env.execute(target)
        pb_env.close()

        joint_delta = float(np.max(np.abs(
            np.array(mj["joint_angles"]) - np.array(pb["joint_angles"]))))
        consistent = (mj["success"] and pb["success"]
                      and joint_delta < JOINT_TOL
                      and mj["collision"] == pb["collision"])
        all_ok &= consistent

        print(f"{str(target):>18} | {mj['joint_error']:>10} | "
              f"{pb['joint_error']:>12} | {joint_delta:>10.4f} | "
              f"{'YES' if consistent else 'NO'}")

    print("-" * 74)
    if all_ok:
        print("SIM-TO-SIM VALIDATION PASSED: skill consistent across MuJoCo and PyBullet")
    else:
        print("SIM-TO-SIM VALIDATION FAILED: engines disagree on at least one target")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
