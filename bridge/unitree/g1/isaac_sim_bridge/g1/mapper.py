"""G1-specific Fabric action → geometry_msgs/Twist mapper."""
from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper, clamp


class G1Mapper(CommandMapper):
    """Maps Fabric actions to Twist commands for Unitree G1 / OM1-sim.

    Velocity limits from OM1-sim deploy.yaml:
      vx: [-0.5, 1.0] m/s   wz: [-0.2, 0.2] rad/s
    """

    def __init__(
        self,
        forward_speed: float = 0.5,
        backward_speed: float = 0.5,
        turn_linear_speed: float = 0.3,
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
            msg.linear.x = clamp(self._fwd, 0.0, 1.0)
        elif a in ("move_backward", "backward"):
            msg.linear.x = -clamp(self._bwd, 0.0, 0.5)
        elif a == "turn_left":
            msg.linear.x = self._turn_lin
            msg.angular.z = clamp(self._turn_ang, 0.0, 0.2)
        elif a == "turn_right":
            msg.linear.x = self._turn_lin
            msg.angular.z = -clamp(self._turn_ang, 0.0, 0.2)
        elif a == "stop":
            pass  # zero Twist
        # unknown action → zero Twist (safe default)
        return msg
