"""MuJoCo torque adapter for the shared X2 inspection policy."""

from __future__ import annotations

import numpy as np

from .control_core import X2InspectionControlCore


INSPECTION_INDICES = np.array([12, 13, 14, 15, 16, 17, 18, 20, 24, 25, 27])


class X2InspectionPolicy(X2InspectionControlCore):
    """PD torque adapter using all 31 official X2 motor channels."""

    def __init__(self, targets: tuple[str, ...], speed_scale: float = 1.0):
        super().__init__(targets, speed_scale)
        self.neutral = np.zeros(31, dtype=np.float64)
        self.neutral[18] = 0.12
        self.neutral[20] = -0.30
        self.neutral[25] = -0.12
        self.neutral[27] = -0.30
        self.kp = np.array([55.0] * 12 + [32.0, 28.0, 28.0, 7.0, 7.0] + [24.0] * 14)
        self.kd = np.array([5.0] * 12 + [2.8, 2.5, 2.5, 0.7, 0.7] + [2.0] * 14)

    def safe_stop_control(self, positions: np.ndarray, velocities: np.ndarray) -> np.ndarray:
        return self.kp * (self.neutral - positions) - self.kd * velocities

    def compute_control(self, observation: dict) -> tuple[np.ndarray, dict]:
        plan = self.compute_plan(observation)
        target = self.neutral.copy()
        if plan.target is not None:
            target[INSPECTION_INDICES] = np.asarray(plan.commanded_inspection_joints)
        positions = np.asarray(observation["joint_positions"])
        velocities = np.asarray(observation["joint_velocities"])
        torque = self.kp * (target - positions) - self.kd * velocities
        return torque, self.diagnostics(plan)
