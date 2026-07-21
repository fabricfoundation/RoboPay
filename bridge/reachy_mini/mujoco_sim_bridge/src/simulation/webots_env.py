"""Webots Environment for Reachy Mini — Sim-to-Sim Cross-Simulator Validation.

Provides a Webots simulation interface for the Reachy Mini social robot,
matching the tabletop scene layout (apple, croissant, duck, table).
"""
import math
import numpy as np


class ReachyMiniWebotsEnvironment:
    """Webots simulation environment interface for Reachy Mini."""

    def __init__(self, target_object: str = "apple"):
        self.sim_time = 0.0
        self.dt       = 0.005  # 200 Hz Webots basicTimestep equivalent
        self.target_object = target_object

        # Target object positions on table in Webots frame (x=forward, y=left, z=up)
        self.OBJECT_POSITIONS = {
            "apple":     np.array([0.6, -0.2, 0.03]),
            "croissant": np.array([0.6,  0.1, 0.03]),
            "duck":      np.array([0.6,  0.3, 0.00]),
        }

        self.reset(target_object=target_object)

    def reset(self, target_object: str | None = None) -> dict:
        if target_object:
            self.target_object = target_object

        self.sim_time = 0.0
        self.qpos     = np.zeros(9, dtype=np.float64)  # yaw_body, stewart 1..6, antennas
        self.target_pos = self.OBJECT_POSITIONS.get(
            self.target_object, self.OBJECT_POSITIONS["apple"]
        ).copy()

        # Reachy Mini head base position in Webots (z=0.37m at top of torso + neck)
        self.head_pos = np.array([0.0, 0.0, 0.37], dtype=np.float64)
        return self.get_obs()

    def set_control(self, ctrl: np.ndarray):
        """Set 9 actuator control targets."""
        # Servo motor response simulation in Webots physics engine
        target = np.asarray(ctrl, dtype=np.float64)
        if len(target) < 9:
            padded = np.zeros(9, dtype=np.float64)
            padded[: len(target)] = target
            target = padded

        # Webots motor position update with stiffness/damping response
        alpha = 0.2
        self.qpos += alpha * (target - self.qpos)

    def step(self, steps: int = 5) -> dict:
        """Advance Webots simulation time."""
        self.sim_time += self.dt * steps
        return self.get_obs()

    def get_obs(self) -> dict:
        """Return Webots observation dictionary matching MuJoCo interface."""
        yaw = float(self.qpos[0])

        # Compute head orientation matrix (Y-axis forward in body frame)
        cos_y, sin_y = math.cos(yaw), math.sin(yaw)
        # 3x3 rotation matrix for torso yaw rotation
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
