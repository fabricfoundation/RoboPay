"""Go2 mapper — placeholder (experimental).

Currently reuses cmd_vel mapping. Replace with Go2-specific SDK backend when available.
"""
from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper, clamp


class Go2Mapper(CommandMapper):
    """Placeholder mapper for Unitree Go2. Experimental — not validated on hardware."""

    def __init__(self, forward_speed=0.5, backward_speed=0.5,
                 turn_angular_speed=0.5):
        self._fwd = forward_speed
        self._bwd = backward_speed
        self._turn_ang = turn_angular_speed

    def map(self, event: ActionEvent) -> Twist:
        msg = Twist()
        a = event.action
        if a in ("move_forward", "forward"):
            msg.linear.x = clamp(self._fwd, 0.0, 1.5)
        elif a in ("move_backward", "backward"):
            msg.linear.x = -clamp(self._bwd, 0.0, 0.5)
        elif a == "turn_left":
            msg.angular.z = clamp(self._turn_ang, 0.0, 1.0)
        elif a == "turn_right":
            msg.angular.z = -clamp(self._turn_ang, 0.0, 1.0)
        elif a == "stop":
            pass
        return msg
