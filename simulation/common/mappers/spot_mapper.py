"""Boston Dynamics Spot MuJoCo mapper."""
from typing import Any, Dict
from .base_mujoco_mapper import MuJoCoCommandMapper, ActuatorCommand


class SpotMapper(MuJoCoCommandMapper):
    """Boston Dynamics Spot — 12 DOF quadruped."""

    def __init__(self):
        super().__init__(n_actuators=12)

    def map(self, action: str, params: Dict[str, Any]) -> ActuatorCommand:
        if action in ("walk", "move_forward"):
            speed = self.clamp(float(params.get("speed", 0.5)), 0.0, 1.0)
            return ActuatorCommand(ctrl=[speed, 0.0, 0.0] + [0.0]*9, duration_sec=3.0, skill=action)
        elif action == "inspect":
            # Lower body + look down
            ctrl = [0.0] * 12
            ctrl[4] = -0.3  # front left knee
            ctrl[7] = -0.3  # front right knee
            return ActuatorCommand(ctrl=ctrl, duration_sec=5.0, skill=action)
        elif action == "dock":
            return ActuatorCommand(ctrl=[0.2, 0.0, 0.0] + [0.0]*9, duration_sec=2.0, skill=action)
        elif action in ("stop", "cancel"):
            return self.zero()
        else:
            return self.zero()
