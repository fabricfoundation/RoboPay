"""ROS2 node for Fabric → sim-arm-01 (2-DOF planar arm, MuJoCo) adapter."""
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist

from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import SimArm01Mapper
from .simulator import SimArm01Simulator


class MuJoCoSimArm01BridgeNode(Node):
    def __init__(self):
        super().__init__("mujoco_bridge_sim_arm_01")

        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")

        p = self.get_parameter
        zenoh_topic   = p("zenoh_topic").get_parameter_value().string_value
        zenoh_listen  = p("zenoh_listen").get_parameter_value().string_value
        cmd_vel_topic = p("cmd_vel_topic").get_parameter_value().string_value

        self._mapper = SimArm01Mapper()
        self._sim    = SimArm01Simulator()
        self._pub    = self.create_publisher(Twist, cmd_vel_topic, 10)

        self._zenoh = ZenohSubscriberHelper(zenoh_listen)
        self._zenoh.subscribe(zenoh_topic, self._on_action)
        self.get_logger().info(f"sim-arm-01 bridge started, subscribed to {zenoh_topic}")

    def _on_action(self, sample):
        raw   = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)
        if event is None:
            self.get_logger().error("Failed to parse action event")
            return

        self.get_logger().info(f"Received action={event.action} params={event.params}")
        self._pub.publish(self._mapper.map(event))

        if event.action == "move_to_pose":
            try:
                target = self._mapper.parse_target(event)
                metrics = self._sim.execute(target)
                self.get_logger().info(f"sim-arm-01 execution: {metrics}")
            except Exception as exc:
                self.get_logger().error(f"Simulation failed: {exc}")

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = MuJoCoSimArm01BridgeNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
