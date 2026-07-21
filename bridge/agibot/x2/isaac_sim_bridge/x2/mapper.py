"""X2-specific Fabric action -> geometry_msgs/Twist mapper.

Supports both direct locomotion commands and skill-based actions
that are routed through the SkillManager.
"""
from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper, clamp


class AgibotX2Mapper(CommandMapper):
    """Maps Fabric actions to Twist commands for AGIBot X2 humanoid.

    Velocity limits for AGIBot X2 humanoid:
      vx: [0, 1.0] m/s   wz: [-0.3, 0.3] rad/s

    Note: AGIBot X2 is a humanoid — forward-only locomotion (no backward).

    Supports:
      - Direct locomotion: move_forward, turn_left, turn_right, stop
      - Skill-based: navigate_to (handled by SkillManager, not here)
    """

    # Actions handled by SkillManager, not by direct mapping
    SKILL_ACTIONS = frozenset({"navigate_to", "skill_step", "skill_cancel"})

    def __init__(
        self,
        forward_speed: float = 0.5,
        turn_linear_speed: float = 0.3,
        turn_angular_speed: float = 0.3,
    ):
        self._fwd = forward_speed
        self._turn_lin = turn_linear_speed
        self._turn_ang = turn_angular_speed

    def is_skill_action(self, action: str) -> bool:
        """Check if this action should be routed to SkillManager."""
        return action in self.SKILL_ACTIONS

    def map(self, event: ActionEvent) -> Twist:
        """Map a direct locomotion ActionEvent to a Twist command.

        For skill-based actions, use SkillManager instead.
        """
        msg = Twist()
        a = event.action

        if a in ("move_forward", "forward"):
            msg.linear.x = clamp(self._fwd, 0.0, 1.0)
        elif a == "turn_left":
            msg.linear.x = self._turn_lin
            msg.angular.z = clamp(self._turn_ang, 0.0, 0.3)
        elif a == "turn_right":
            msg.linear.x = self._turn_lin
            msg.angular.z = -clamp(self._turn_ang, 0.0, 0.3)
        elif a == "stop":
            pass  # zero Twist
        # unknown action -> zero Twist (safe default)
        return msg

    def skill_result_to_twist(self, action: str, speed: float, angular: float) -> Twist:
        """Convert skill result to Twist command.

        Args:
            action: Skill action name (move_forward, turn_left, etc.)
            speed: Linear speed from skill
            angular: Angular speed from skill
        """
        msg = Twist()
        if action == "move_forward":
            msg.linear.x = clamp(speed, 0.0, 1.0)
            msg.angular.z = clamp(angular, -0.3, 0.3)
        elif action == "turn_left":
            msg.linear.x = clamp(speed, 0.0, 0.5)
            msg.angular.z = clamp(angular, 0.0, 0.3)
        elif action == "turn_right":
            msg.linear.x = clamp(speed, 0.0, 0.5)
            msg.angular.z = clamp(angular, -0.3, 0.0)
        elif action == "stop":
            pass  # zero Twist
        return msg
