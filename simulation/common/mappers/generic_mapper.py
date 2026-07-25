"""Generic MuJoCo mapper for robots without specific implementations."""
from typing import Any, Dict
from .base_mujoco_mapper import MuJoCoCommandMapper, ActuatorCommand


class GenericMapper(MuJoCoCommandMapper):
    """Generic mapper — uses first 3 actuators as vx, vy, wz."""

    def __init__(self, n_actuators: int = 12):
        super().__init__(n_actuators=n_actuators)

    def map(self, action: str, params: Dict[str, Any]) -> ActuatorCommand:
        if action in ("move_forward", "forward", "walk"):
            speed = self.clamp(float(params.get("speed", 0.5)), 0.0, 1.0)
            ctrl = [0.0] * self.n_actuators
            ctrl[0] = speed
            return ActuatorCommand(ctrl=ctrl, duration_sec=3.0, skill=action)
        elif action in ("move_backward", "backward"):
            speed = self.clamp(float(params.get("speed", 0.3)), 0.0, 0.5)
            ctrl = [0.0] * self.n_actuators
            ctrl[0] = -speed
            return ActuatorCommand(ctrl=ctrl, duration_sec=3.0, skill=action)
        elif action in ("turn_left",):
            ctrl = [0.0] * self.n_actuators
            ctrl[2] = 0.5
            return ActuatorCommand(ctrl=ctrl, duration_sec=2.0, skill=action)
        elif action in ("turn_right",):
            ctrl = [0.0] * self.n_actuators
            ctrl[2] = -0.5
            return ActuatorCommand(ctrl=ctrl, duration_sec=2.0, skill=action)
        elif action in ("navigate", "navigate_obstacle"):
            ctrl = [0.0] * self.n_actuators
            return ActuatorCommand(ctrl=ctrl, duration_sec=0.0, skill=action)
        elif action in ("stop", "cancel"):
            return self.zero()
        else:
            return self.zero()
