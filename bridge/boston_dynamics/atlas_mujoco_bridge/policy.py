"""MuJoCo torque adapter for the shared Atlas controller.

Phase 2: Uses dict-based joint targets from control_core.
"""

from __future__ import annotations

import numpy as np

from .control_core import AtlasObstacleControlCore, ACTUATOR_ORDER, NEUTRAL_POSE, EFFORT_LIMITS, KP, KD


class AtlasObstaclePolicy(AtlasObstacleControlCore):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.neutral_joint_targets = np.array(
            [NEUTRAL_POSE[name] for name in ACTUATOR_ORDER], dtype=np.float64
        )

    def desired_joints(self, observation: dict) -> tuple[np.ndarray, dict]:
        plan = self.compute_plan(observation)
        desired = self.neutral_joint_targets.copy()
        if plan.phase != "GOAL_REACHED" and plan.desired_joints:
            for i, name in enumerate(ACTUATOR_ORDER):
                if name in plan.desired_joints:
                    desired[i] = plan.desired_joints[name]
        return desired, self.diagnostics(plan)

    def torque(
        self,
        desired: np.ndarray,
        positions: np.ndarray,
        velocities: np.ndarray,
        limits: np.ndarray,
    ) -> np.ndarray:
        command = KP * (desired - positions) - KD * velocities
        effort = np.array([EFFORT_LIMITS.get(name, 100) for name in ACTUATOR_ORDER])
        normalized = command / effort
        return np.clip(normalized, -1.0, 1.0)
