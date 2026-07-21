"""ROS2 node for Fabric → Dobot CRA adapter."""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import CraMapper


class IsaacSimCraBridgeNode(Node):
    def __init__(self):
        super().__init__("isaac_sim_bridge_cra")

        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("forward_speed", 0.3)
        self.declare_parameter("backward_speed", 0.0)
        self.declare_parameter("turn_linear_speed", 0.1)
        self.declare_parameter("turn_angular_speed", 0.2)

        p = self.get_parameter
        zenoh_topic   = p("zenoh_topic").get_parameter_value().string_value
        zenoh_listen  = p("zenoh_listen").get_parameter_value().string_value
        cmd_vel_topic = p("cmd_vel_topic").get_parameter_value().string_value

        self._mapper = CraMapper(
            forward_speed      = p("forward_speed").get_parameter_value().double_value,
            backward_speed     = p("backward_speed").get_parameter_value().double_value,
            turn_linear_speed  = p("turn_linear_speed").get_parameter_value().double_value,
            turn_angular_speed = p("turn_angular_speed").get_parameter_value().double_value,
        )
        self._pub = self.create_publisher(Twist, cmd_vel_topic, 10)
        self.get_logger().info(f"Adapter started, publishing to {cmd_vel_topic}")

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
        twist = self._mapper.map(event)
        self._pub.publish(twist)
        self.get_logger().info(
            f"Published /cmd_vel: linear.x={twist.linear.x:.2f} angular.z={twist.angular.z:.2f}"
        )

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = IsaacSimCraBridgeNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
