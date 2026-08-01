"""ROS2 node for Fabric -> Unitree G1 (OM1-sim) adapter."""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import G1Mapper

# Joint order matches the G1 29-DOF right-arm chain used by the
# door-opening skill (validated in simulation, see project README).
RIGHT_ARM_JOINTS = [
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
]

# Reach-target joint angles (radians) validated in MuJoCo simulation,
# producing an 82.4 degree door hinge rotation.
DOOR_OPEN_TARGET_ANGLES = [0.2, -0.2, 0.1, -1.5, 0.0, 0.3, 0.0]


class IsaacSimG1BridgeNode(Node):
    def __init__(self):
        super().__init__("isaac_sim_bridge_g1")

        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("arm_trajectory_topic", "/right_arm_controller/joint_trajectory")
        self.declare_parameter("forward_speed", 0.5)
        self.declare_parameter("backward_speed", 0.5)
        self.declare_parameter("turn_linear_speed", 0.3)
        self.declare_parameter("turn_angular_speed", 0.2)

        p = self.get_parameter
        zenoh_topic = p("zenoh_topic").get_parameter_value().string_value
        zenoh_listen = p("zenoh_listen").get_parameter_value().string_value
        cmd_vel_topic = p("cmd_vel_topic").get_parameter_value().string_value
        arm_trajectory_topic = p("arm_trajectory_topic").get_parameter_value().string_value

        self._mapper = G1Mapper(
            forward_speed = p("forward_speed").get_parameter_value().double_value,
            backward_speed = p("backward_speed").get_parameter_value().double_value,
            turn_linear_speed = p("turn_linear_speed").get_parameter_value().double_value,
            turn_angular_speed = p("turn_angular_speed").get_parameter_value().double_value,
        )
        self._pub = self.create_publisher(Twist, cmd_vel_topic, 10)
        self._arm_pub = self.create_publisher(JointTrajectory, arm_trajectory_topic, 10)
        self.get_logger().info(f"Adapter started, publishing to {cmd_vel_topic}")
        self.get_logger().info(f"Arm trajectory publishing to {arm_trajectory_topic}")

        self._zenoh = ZenohSubscriberHelper(zenoh_listen)
        self._zenoh.subscribe(zenoh_topic, self._on_action)
        self.get_logger().info(f"Subscribed to Zenoh topic: {zenoh_topic}")

    def _on_action(self, sample):
        raw = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)
        if event is None:
            self.get_logger().error("Failed to parse action event")
            return

        self.get_logger().info(f"Received action={event.action} params={event.params}")

        if event.action in ("open_door", "open_door_handle"):
            self._publish_arm_trajectory()
            return

        twist = self._mapper.map(event)
        self._pub.publish(twist)
        self.get_logger().info(
            f"Published /cmd_vel: linear.x={twist.linear.x:.2f} angular.z={twist.angular.z:.2f}"
        )

    def _publish_arm_trajectory(self):
        traj = JointTrajectory()
        traj.joint_names = RIGHT_ARM_JOINTS
        point = JointTrajectoryPoint()
        point.positions = DOOR_OPEN_TARGET_ANGLES
        point.time_from_start.sec = 3
        traj.points = [point]
        self._arm_pub.publish(traj)
        self.get_logger().info(
            f"Published door-opening arm trajectory to {len(RIGHT_ARM_JOINTS)} joints"
        )

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = IsaacSimG1BridgeNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
