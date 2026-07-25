"""Unitree G1 humanoid MuJoCo mapper.

G1 has 29 DOF. For locomotion we use:
- ctrl[0]: forward velocity (vx)
- ctrl[1]: lateral velocity (vy)
- ctrl[2]: yaw rate (wz)
Higher indices control individual joints for more complex motions.
"""
from typing import Any, Dict
from .base_mujoco_mapper import MuJoCoCommandMapper, ActuatorCommand


class G1Mapper(MuJoCoCommandMapper):
    """Unitree G1 humanoid — 29 DOF, bipedal locomotion + manipulation."""

    def __init__(self):
        super().__init__(n_actuators=29)

    def map(self, action: str, params: Dict[str, Any]) -> ActuatorCommand:
        if action in ("move_forward", "forward"):
            speed = self.clamp(float(params.get("speed", 0.5)), 0.0, 1.0)
            return ActuatorCommand(
                ctrl=[speed, 0.0, 0.0] + [0.0] * 26,
                duration_sec=float(params.get("durationSec", 3.0)),
                skill=action,
            )
        elif action in ("move_backward", "backward"):
            speed = self.clamp(float(params.get("speed", 0.3)), 0.0, 0.5)
            return ActuatorCommand(
                ctrl=[-speed, 0.0, 0.0] + [0.0] * 26,
                duration_sec=float(params.get("durationSec", 3.0)),
                skill=action,
            )
        elif action == "turn_left":
            return ActuatorCommand(
                ctrl=[0.0, 0.0, 0.5] + [0.0] * 26,
                duration_sec=float(params.get("durationSec", 2.0)),
                skill=action,
            )
        elif action == "turn_right":
            return ActuatorCommand(
                ctrl=[0.0, 0.0, -0.5] + [0.0] * 26,
                duration_sec=float(params.get("durationSec", 2.0)),
                skill=action,
            )
        elif action == "wave":
            # Raise right arm (shoulder pitch actuator)
            ctrl = [0.0] * 29
            ctrl[12] = -1.5  # right shoulder pitch
            return ActuatorCommand(ctrl=ctrl, duration_sec=2.0, skill=action)
        elif action == "navigate_obstacle":
            goal_x = float(params.get("goal_x", 5.0))
            goal_y = float(params.get("goal_y", 3.0))
            return ActuatorCommand(
                ctrl=[0.0] * 29,  # planner overrides each step
                duration_sec=0.0,  # continuous until goal
                skill=action,
                metrics_fn=lambda data, sx, sy: {
                    "distance_to_goal": ((goal_x - data.qpos[0])**2 + (goal_y - data.qpos[1])**2)**0.5,
                    "goal_reached": ((goal_x - data.qpos[0])**2 + (goal_y - data.qpos[1])**2)**0.5 < 0.3,
                },
            )
        elif action in ("stop", "cancel"):
            return self.zero()
        else:
            return self.zero()
