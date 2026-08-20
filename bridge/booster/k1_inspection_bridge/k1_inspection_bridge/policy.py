"""MuJoCo torque adapter for the shared K1 inspection policy."""

from __future__ import annotations

import numpy as np

from .control_core import K1InspectionControlCore


class K1InspectionPolicy(K1InspectionControlCore):
    """PD torque adapter using all 22 official K1 motor channels."""

    def __init__(self, targets: tuple[str, ...], speed_scale: float = 1.0):
        super().__init__(targets, speed_scale)
        self.neutral = np.zeros(22, dtype=np.float64)
        self.neutral[3] = -1.30
        self.neutral[7] = 1.30
        self.kp = np.array([18.0, 18.0] + [12.0] * 8 + [30.0, 21.0, 18.0, 40.0, 28.0, 28.0] * 2)
        self.kd = np.array([1.2, 1.2] + [0.8] * 8 + [3.6, 2.6, 2.2, 4.8, 4.2, 4.2] * 2)

    def safe_stop_control(self, positions: np.ndarray, velocities: np.ndarray) -> np.ndarray:
        return self.kp * (self.neutral - positions) - self.kd * velocities

    def compute_control(self, observation: dict) -> tuple[np.ndarray, dict]:
        plan = self.compute_plan(observation)
        target = self.neutral.copy()
        if plan.target is not None:
            target[:10] = np.asarray(plan.commanded_upper_joints)
        positions = np.asarray(observation["joint_positions"])
        velocities = np.asarray(observation["joint_velocities"])
        torque = self.kp * (target - positions) - self.kd * velocities
        return torque, self.diagnostics(plan)
