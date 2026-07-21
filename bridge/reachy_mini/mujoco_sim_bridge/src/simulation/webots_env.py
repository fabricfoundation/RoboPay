"""Webots Cross-Simulator Environment for Reachy Mini Sim-to-Sim Validation."""
import math
import numpy as np


class ReachyMiniWebotsEnvironment:
    """Webots simulation environment interface for Reachy Mini cross-simulator validation."""

    def __init__(self, target_object: str = "apple"):
        self.sim_time = 0.0
        self.dt       = 0.005
        self.target_object = target_object

        self.OBJECT_POSITIONS = {
            "apple":     np.array([0.6, -0.2, 0.03]),
            "croissant": np.array([0.6,  0.1, 0.03]),
            "duck":      np.array([0.6,  0.3, 0.00]),
        }
        self.reset(target_object=target_object)

    def reset(self, target_object: str | None = None) -> dict:
        if target_object:
            self.target_object = target_object

        self.sim_time   = 0.0
        self.qpos       = np.zeros(9, dtype=np.float64)
        self.target_pos = self.OBJECT_POSITIONS.get(
            self.target_object, self.OBJECT_POSITIONS["apple"]
        ).copy()
        self.head_pos   = np.array([0.0, 0.0, 0.37], dtype=np.float64)
        return self.get_obs()

    def set_control(self, ctrl: np.ndarray):
        target = np.asarray(ctrl, dtype=np.float64)
        if len(target) < 9:
            padded = np.zeros(9, dtype=np.float64)
            padded[: len(target)] = target
            target = padded
        self.qpos += 0.2 * (target - self.qpos)

    def step(self, steps: int = 5) -> dict:
        self.sim_time += self.dt * steps
        return self.get_obs()

    def get_obs(self) -> dict:
        yaw = float(self.qpos[0])
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)
        xmat = np.array([
            [cos_y, -sin_y, 0.0],
            [sin_y,  cos_y, 0.0],
            [  0.0,    0.0, 1.0],
        ], dtype=np.float64).flatten()

        return {
            "sim_time":      self.sim_time,
            "head_pos":      self.head_pos.copy(),
            "head_xmat":     xmat,
            "base_yaw":      yaw,
            "apple_pos":     self.OBJECT_POSITIONS["apple"].copy(),
            "croissant_pos": self.OBJECT_POSITIONS["croissant"].copy(),
            "duck_pos":      self.OBJECT_POSITIONS["duck"].copy(),
            "target_pos":    self.target_pos.copy(),
            "stewart_qpos":  self.qpos[1:7].copy(),
            "antenna_qpos":  self.qpos[7:9].copy(),
            "num_contacts":  1,
            "engine":        "Webots",
        }
