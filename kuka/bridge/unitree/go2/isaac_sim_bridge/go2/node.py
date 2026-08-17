"""ROS2 node for Fabric → Unitree Go2 adapter (placeholder)."""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import Go2Mapper


class IsaacSimGo2BridgeNode(Node):
    def __init__(self):
        super().__init__("isaac_sim_bridge_go2")
        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("forward_speed", 0.5)
        self.declare_parameter("backward_speed", 0.5)
        self.declare_parameter("turn_angular_speed", 0.5)

        p = self.get_parameter
        zenoh_topic   = p("zenoh_topic").get_parameter_value().string_value
        zenoh_listen  = p("zenoh_listen").get_parameter_value().string_value
        cmd_vel_topic = p("cmd_vel_topic").get_parameter_value().string_value

        self._mapper = Go2Mapper(
            forward_speed     = p("forward_speed").get_parameter_value().double_value,
            backward_speed    = p("backward_speed").get_parameter_value().double_value,
            turn_angular_speed= p("turn_angular_speed").get_parameter_value().double_value,
        )
        self._pub = self.create_publisher(Twist, cmd_vel_topic, 10)
        self._zenoh = ZenohSubscriberHelper(zenoh_listen)
        self._zenoh.subscribe(zenoh_topic, self._on_action)
        self.get_logger().info(f"Go2 adapter ready, subscribed to {zenoh_topic}")

    def _on_action(self, sample):
        event = parse_action_event(bytes(sample.payload.to_bytes()))
        if event is None:
            return
        self._pub.publish(self._mapper.map(event))

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = IsaacSimGo2BridgeNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
