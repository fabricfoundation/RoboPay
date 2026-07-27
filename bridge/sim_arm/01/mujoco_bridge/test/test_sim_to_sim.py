"""Sim-to-sim test: MuJoCo vs PyBullet converge to the same joint pose.

Skipped automatically if pybullet is not installed, so the core CI (mujoco-only)
stays green while a pybullet-enabled run exercises cross-engine consistency.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sim_arm_01.simulator import SimArm01Simulator

pytest.importorskip("pybullet")
from sim_arm_01.pybullet_simulator import SimArm01PyBullet   # noqa: E402

JOINT_TOL = 0.05


@pytest.mark.parametrize("target", [[1.0, -0.5], [0.5, 0.5], [-1.2, 0.8], [2.0, -1.5]])
def test_engines_agree(target):
    mj = SimArm01Simulator().execute(target)
    pb_env = SimArm01PyBullet()
    pb = pb_env.execute(target)
    pb_env.close()

    assert mj["success"] and pb["success"], f"both engines must reach {target}"
    delta = float(np.max(np.abs(
        np.array(mj["joint_angles"]) - np.array(pb["joint_angles"]))))
    assert delta < JOINT_TOL, f"engine disagreement {delta:.4f} >= {JOINT_TOL}"
    assert mj["collision"] == pb["collision"]
