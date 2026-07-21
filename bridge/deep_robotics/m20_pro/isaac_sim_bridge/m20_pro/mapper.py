"""Deep Robotics M20 Pro Mapper — maps Fabric actions to geometry_msgs/Twist.

M20 Pro is a quadruped robot with the following constraints:
  - Forward: vx ∈ [0, 1.5] m/s
  - Backward: vx ∈ [-1.5, 0] m/s (quadruped can walk backward)
  - Angular: wz ∈ [-1.0, 1.0] rad/s
"""
from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper, clamp


class M20ProMapper(CommandMapper):
    """Maps Fabric actions to Twist commands for Deep Robotics M20 Pro.

    M20 Pro is a quadruped — can walk forward and backward.
    """

    def __init__(
        self,
        forward_speed: float = 1.0,
        backward_speed: float = 0.5,
        turn_angular_speed: float = 0.8,
    ):
        self._fwd = forward_speed
        self._bwd = backward_speed
        self._turn_ang = turn_angular_speed

    def map(self, event: ActionEvent) -> Twist:
        msg = Twist()
        a = event.action
        if a in ("move_forward", "forward"):
            msg.linear.x = clamp(self._fwd, 0.0, 1.5)
        elif a in ("move_backward", "backward"):
            msg.linear.x = -clamp(self._bwd, 0.0, 1.5)
        elif a == "turn_left":
            msg.angular.z = clamp(self._turn_ang, 0.0, 1.0)
        elif a == "turn_right":
            msg.angular.z = -clamp(self._turn_ang, 0.0, 1.0)
        elif a == "stop":
            pass  # zero Twist
        # unknown action → zero Twist (safe default)
        return msg
