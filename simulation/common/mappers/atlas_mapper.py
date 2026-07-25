"""Boston Dynamics Atlas MuJoCo mapper."""
from typing import Any, Dict
from .base_mujoco_mapper import MuJoCoCommandMapper, ActuatorCommand


class AtlasMapper(MuJoCoCommandMapper):
    """Boston Dynamics Atlas — humanoid, 30+ DOF."""

    def __init__(self):
        super().__init__(n_actuators=30)

    def map(self, action: str, params: Dict[str, Any]) -> ActuatorCommand:
        if action in ("walk", "move_forward"):
            speed = self.clamp(float(params.get("speed", 0.5)), 0.0, 1.0)
            return ActuatorCommand(ctrl=[speed, 0.0, 0.0] + [0.0]*27, duration_sec=3.0, skill=action)
        elif action == "climb_stairs":
            return ActuatorCommand(ctrl=[0.3, 0.0, 0.0] + [0.0]*27, duration_sec=10.0, skill=action)
        elif action in ("stop", "cancel"):
            return self.zero()
        else:
            return self.zero()
