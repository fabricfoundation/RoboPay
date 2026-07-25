"""Unitree Go2 quadruped MuJoCo mapper.

Go2 has 12 DOF (3 per leg). For locomotion:
- ctrl[0]: forward velocity
- ctrl[1]: lateral velocity
- ctrl[2]: yaw rate
"""
from typing import Any, Dict
from .base_mujoco_mapper import MuJoCoCommandMapper, ActuatorCommand


class Go2Mapper(MuJoCoCommandMapper):
    """Unitree Go2 quadruped — 12 DOF, trotting gait."""

    def __init__(self):
        super().__init__(n_actuators=12)

    def map(self, action: str, params: Dict[str, Any]) -> ActuatorCommand:
        if action in ("move_forward", "forward"):
            speed = self.clamp(float(params.get("speed", 0.5)), 0.0, 1.5)
            return ActuatorCommand(ctrl=[speed, 0.0, 0.0] + [0.0]*9, duration_sec=3.0, skill=action)
        elif action in ("move_backward", "backward"):
            speed = self.clamp(float(params.get("speed", 0.3)), 0.0, 0.5)
            return ActuatorCommand(ctrl=[-speed, 0.0, 0.0] + [0.0]*9, duration_sec=3.0, skill=action)
        elif action == "turn_left":
            return ActuatorCommand(ctrl=[0.0, 0.0, 0.5] + [0.0]*9, duration_sec=2.0, skill=action)
        elif action == "turn_right":
            return ActuatorCommand(ctrl=[0.0, 0.0, -0.5] + [0.0]*9, duration_sec=2.0, skill=action)
        elif action in ("stop", "cancel"):
            return self.zero()
        else:
            return self.zero()
