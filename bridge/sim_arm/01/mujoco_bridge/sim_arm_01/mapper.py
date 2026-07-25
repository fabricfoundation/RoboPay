"""Maps paid RoboPay move_to_pose actions to joint targets for the 2-DOF planar arm."""
import json
from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper, clamp


class SimArm01Mapper(CommandMapper):
    """Maps Fabric actions to joint targets for sim-arm-01.

    Supported skillId: move_to_pose
      params: {"target_qpos": [q1, q2]}  — joint angles in radians
    """

    def map(self, event: ActionEvent) -> Twist:
        # Return a zero Twist; actual execution is handled by the simulator
        return Twist()

    def parse_target(self, event: ActionEvent) -> list:
        """Extract target_qpos from event params."""
        params = event.params if isinstance(event.params, dict) else json.loads(event.params or "{}")
        raw = params.get("target_qpos", [0.0, 0.0])
        return [clamp(float(raw[0]), -3.14, 3.14), clamp(float(raw[1]), -3.14, 3.14)]
