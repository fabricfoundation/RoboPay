"""MuJoCo torque adapter for the shared G1 inspection policy."""

from __future__ import annotations

import numpy as np

from .control_core import G1InspectionControlCore


INSPECTION_INDICES = np.array([12, 13, 14, 15, 16, 18, 20, 22, 23, 25, 27])


class G1InspectionPolicy(G1InspectionControlCore):
    """PD torque adapter using all 29 official G1 motor channels."""

    def __init__(self, targets: tuple[str, ...], speed_scale: float = 1.0):
        super().__init__(targets, speed_scale)
        self.neutral = np.zeros(29, dtype=np.float64)
        self.neutral[18] = 0.25
        self.neutral[25] = 0.25
        self.kp = np.array([55.0] * 12 + [32.0, 28.0, 28.0] + [24.0] * 14)
        self.kd = np.array([5.0] * 12 + [2.8, 2.5, 2.5] + [2.0] * 14)

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
