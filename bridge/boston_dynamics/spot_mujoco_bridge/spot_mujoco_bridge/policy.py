"""MuJoCo actuator adapter for the shared Spot obstacle-avoidance policy."""

from __future__ import annotations

import numpy as np

from .control_core import SpotObstacleControlCore

class SpotObstaclePolicy(SpotObstacleControlCore):
    """Closed-loop planar navigator with a trot-like diagonal gait.

    The model's position actuators are the only outputs.  In particular, the
    free base is never written to, so forward progress and obstacle contacts
    come from the simulation physics.
    """

    def __init__(
        self,
        goal: tuple[float, float],
        side: str = "left",
        reference_route: tuple[tuple[float, float], ...] | None = None,
        speed_scale: float = 1.0,
    ):
        super().__init__(goal, side, reference_route, speed_scale)
        self._home_control = np.array(
            [0.0, 1.04, -1.80] * 4, dtype=np.float64
        )

    def safe_stop_control(self) -> np.ndarray:
        """Return the neutral position command used by the simulator stop path."""

        return self._home_control.copy()

    def compute_control(self, observation: dict) -> tuple[np.ndarray, dict]:
        """Return a 12-actuator command and state-derived diagnostics."""

        plan = self.compute_plan(observation)
        control = self._home_control.copy()
        if plan.phase == "GOAL_REACHED":
            return control, self.diagnostics(plan)
        for leg, (hip_offset, elbow_offset) in enumerate(
            zip(plan.hip_rotation_offsets, plan.elbow_lift_offsets, strict=True)
        ):
            base = leg * 3
            control[base + 1] += hip_offset
            control[base + 2] += elbow_offset
        return control, self.diagnostics(plan)
