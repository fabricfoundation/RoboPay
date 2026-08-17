"""MuJoCo torque adapter for the shared Go2 controller."""

from __future__ import annotations

import math
import numpy as np

from .control_core import Go2ObstacleControlCore


LEG_LINK_LENGTH_M = 0.213


def foot_inverse_kinematics(x: float, z: float) -> tuple[float, float]:
    """Solve the official Go2 planar thigh/calf chain (knee flexes negative)."""

    radius = min(2.0 * LEG_LINK_LENGTH_M - 0.001, max(0.08, math.hypot(x, z)))
    cosine = (radius * radius - 2.0 * LEG_LINK_LENGTH_M**2) / (2.0 * LEG_LINK_LENGTH_M**2)
    calf = -math.acos(max(-1.0, min(1.0, cosine)))
    thigh = math.atan2(-x, -z) - calf / 2.0
    return thigh, calf


class Go2ObstaclePolicy(Go2ObstacleControlCore):
    """Creates desired joints, then PD torque for the official torque motors."""

    KP = 45.0
    KD = 1.5

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        neutral_thigh, neutral_calf = foot_inverse_kinematics(0.0, self.STANCE_HEIGHT_M)
        self.neutral_joint_targets = np.array([0.0, neutral_thigh, neutral_calf] * 4, dtype=np.float64)

    def desired_joints(self, observation: dict) -> tuple[np.ndarray, dict]:
        plan = self.compute_plan(observation)
        desired = self.neutral_joint_targets.copy()
        if plan.phase != "GOAL_REACHED":
            for leg, (x, z) in enumerate(zip(plan.foot_x_m, plan.foot_z_m, strict=True)):
                desired[3 * leg + 1], desired[3 * leg + 2] = foot_inverse_kinematics(x, z)
        return desired, self.diagnostics(plan)

    def torque(self, desired: np.ndarray, positions: np.ndarray, velocities: np.ndarray, limits: np.ndarray) -> np.ndarray:
        command = self.KP * (desired - positions) - self.KD * velocities
        return np.clip(command, limits[:, 0], limits[:, 1])
