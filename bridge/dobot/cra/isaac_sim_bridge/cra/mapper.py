"""Dobot CRA Mapper — maps Fabric actions to geometry_msgs/Twist.

Dobot CRA is a collaborative robot arm with the following constraints:
  - Linear:  vx ∈ [0, 0.5] m/s  (end-effector forward, no reverse)
  - Angular: wz ∈ [-0.3, 0.3] rad/s
"""
from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper, clamp


class CraMapper(CommandMapper):
    """Maps Fabric actions to Twist commands for Dobot CRA robot arm.

    CRA is a collaborative robot arm — forward-only end-effector motion (vx >= 0).
    """

    def __init__(
        self,
        forward_speed: float = 0.3,
        backward_speed: float = 0.0,
        turn_linear_speed: float = 0.1,
        turn_angular_speed: float = 0.2,
    ):
        self._fwd = forward_speed
        self._bwd = backward_speed
        self._turn_lin = turn_linear_speed
        self._turn_ang = turn_angular_speed

    def map(self, event: ActionEvent) -> Twist:
        msg = Twist()
        a = event.action
        if a in ("move_forward", "forward"):
            msg.linear.x = clamp(self._fwd, 0.0, 0.5)
        elif a in ("move_backward", "backward"):
            # CRA arm has no reverse end-effector travel — clamp to 0
            msg.linear.x = 0.0
        elif a == "turn_left":
            msg.linear.x = self._turn_lin
            msg.angular.z = clamp(self._turn_ang, 0.0, 0.3)
        elif a == "turn_right":
            msg.linear.x = self._turn_lin
            msg.angular.z = -clamp(self._turn_ang, 0.0, 0.3)
        elif a == "stop":
            pass  # zero Twist
        # unknown action → zero Twist (safe default)
        return msg
