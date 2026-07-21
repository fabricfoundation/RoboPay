"""ROS2 node for Fabric -> Deep Robotics X30 Pro adapter."""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import X30ProMapper


class IsaacSimX30ProBridgeNode(Node):
    def __init__(self):
        super().__init__("isaac_sim_bridge_x30_pro")
        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("forward_speed", 0.5)
        self.declare_parameter("backward_speed", 0.5)
        self.declare_parameter("turn_angular_speed", 0.5)

        p = self.get_parameter
        self._mapper = X30ProMapper(
            forward_speed=p("forward_speed").get_parameter_value().double_value,
            backward_speed=p("backward_speed").get_parameter_value().double_value,
            turn_angular_speed=p("turn_angular_speed").get_parameter_value().double_value,
        )
        self._pub = self.create_publisher(Twist, p("cmd_vel_topic").get_parameter_value().string_value, 10)
        self._zenoh = ZenohSubscriberHelper(p("zenoh_listen").get_parameter_value().string_value)
        self._zenoh.subscribe(p("zenoh_topic").get_parameter_value().string_value, self._on_action)
        self.get_logger().info("Adapter started")

    def _on_action(self, sample):
        event = parse_action_event(bytes(sample.payload.to_bytes()))
        if event is None:
            self.get_logger().error("Failed to parse action event")
            return
        twist = self._mapper.map(event)
        self._pub.publish(twist)

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = IsaacSimX30ProBridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
