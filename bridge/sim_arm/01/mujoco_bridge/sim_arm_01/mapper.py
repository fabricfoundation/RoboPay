"""Maps paid RoboPay move_to_pose actions to joint targets for the 2-DOF planar arm."""
import json
from geometry_msgs.msg import Twist
from zenoh_bridge import ActionEvent, CommandMapper


class SimArm01Mapper(CommandMapper):
    """Maps Fabric actions to joint targets for sim-arm-01.

    Supported action: move_to_pose
      params: {"target_qpos": [q1, q2]}  — joint angles in radians

    sim-arm-01 is a fixed-base joint-controlled arm, so /cmd_vel (a mobile-base
    Twist) does not apply: map() returns a zero Twist and actuation happens via
    the joint servo in SimArm01Simulator.
    """

    def map(self, event: ActionEvent) -> Twist:
        return Twist()

    def parse_target(self, event: ActionEvent) -> list:
        """Extract target_qpos verbatim (NOT clamped).

        Clamping the target here would silently turn an out-of-range (physically
        unreachable) request into a reachable one and mask genuine failures. The
        actuator itself enforces joint limits; the success check compares the
        real reached pose against the *requested* target, so an unreachable pose
        is honestly reported as a failure.
        """
        params = event.params if isinstance(event.params, dict) \
            else json.loads(event.params or "{}")
        raw = params.get("target_qpos", [0.0, 0.0])
        return [float(raw[0]), float(raw[1])]
