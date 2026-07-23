from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper, clamp

class X2Mapper(CommandMapper):
    """Map paid RoboPay actions to conservative X2 base velocity commands."""
    def __init__(self, forward_speed=0.4, backward_speed=0.25, turn_angular_speed=0.2):
        self.forward_speed, self.backward_speed, self.turn_angular_speed = forward_speed, backward_speed, turn_angular_speed
    def map(self, event: ActionEvent) -> Twist:
        msg = Twist(); action = event.action.lower(); speed = float(event.params.get("speed", self.forward_speed))
        if action in ("move_forward", "forward"): msg.linear.x = clamp(speed, 0.0, 0.5)
        elif action in ("move_backward", "backward"): msg.linear.x = -clamp(speed, 0.0, 0.3)
        elif action == "turn_left": msg.angular.z = clamp(speed or self.turn_angular_speed, 0.0, 0.2)
        elif action == "turn_right": msg.angular.z = -clamp(speed or self.turn_angular_speed, 0.0, 0.2)
        # standing_balance, wave_arm, stop, and unknown actions are stationary by design.
        return msg
