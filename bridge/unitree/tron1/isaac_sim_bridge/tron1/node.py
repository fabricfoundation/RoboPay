"""ROS2 node for Fabric -> LimX TRON1 humanoid adapter.

Supports both direct locomotion commands and policy-driven skills.
Skills publish metrics to /robopay/metrics for observability.
"""
import json
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from geometry_msgs.msg import Twist
from std_msgs.msg import String

from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import Tron1Mapper
from .skills import SkillManager


class IsaacSimTron1BridgeNode(Node):
    """ROS2 bridge between Fabric tunnel actions and Isaac Sim TRON1.

    Two modes:
      1. Direct locomotion: action -> mapper -> /cmd_vel
      2. Skill-based: action -> skill_manager -> mapper -> /cmd_vel + /robopay/metrics
    """

    def __init__(self):
        super().__init__("isaac_sim_bridge_tron1")

        # Parameters
        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("metrics_topic", "/robopay/metrics")
        self.declare_parameter("forward_speed", 0.3)
        self.declare_parameter("turn_linear_speed", 0.15)
        self.declare_parameter("turn_angular_speed", 0.3)

        p = self.get_parameter
        zenoh_topic = p("zenoh_topic").get_parameter_value().string_value
        zenoh_listen = p("zenoh_listen").get_parameter_value().string_value
        cmd_vel_topic = p("cmd_vel_topic").get_parameter_value().string_value
        metrics_topic = p("metrics_topic").get_parameter_value().string_value

        # Mapper for direct locomotion
        self._mapper = Tron1Mapper(
            forward_speed=p("forward_speed").get_parameter_value().double_value,
            turn_linear_speed=p("turn_linear_speed").get_parameter_value().double_value,
            turn_angular_speed=p("turn_angular_speed").get_parameter_value().double_value,
        )

        # Skill manager for policy-driven actions
        self._skill_manager = SkillManager(
            metrics_callback=self._publish_metrics
        )

        # Publishers
        self._cmd_vel_pub = self.create_publisher(Twist, cmd_vel_topic, 10)
        self._metrics_pub = self.create_publisher(
            String, metrics_topic,
            QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT),
        )

        self.get_logger().info(f"Adapter started, publishing to {cmd_vel_topic}")
        self.get_logger().info(f"Metrics published to {metrics_topic}")

        # Zenoh subscriber
        self._zenoh = ZenohSubscriberHelper(zenoh_listen)
        self._zenoh.subscribe(zenoh_topic, self._on_action)
        self.get_logger().info(f"Subscribed to Zenoh topic: {zenoh_topic}")

        # Timer for skill execution loop (20Hz)
        self._skill_timer = self.create_timer(0.05, self._skill_tick)

    def _on_action(self, sample):
        """Handle incoming action from Fabric tunnel."""
        raw = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)
        if event is None:
            self.get_logger().error("Failed to parse action event")
            return

        self.get_logger().info(f"Received action={event.action} params={event.params}")

        # Route to skill manager or direct mapper
        if self._mapper.is_skill_action(event.action):
            result = self._skill_manager.execute(event)
            self.get_logger().info(f"Skill result: {result}")
        else:
            # Direct locomotion command
            twist = self._mapper.map(event)
            self._cmd_vel_pub.publish(twist)
            self.get_logger().info(
                f"Published /cmd_vel: linear.x={twist.linear.x:.2f} "
                f"angular.z={twist.angular.z:.2f}"
            )

    def _skill_tick(self):
        """Run active skill step at 20Hz."""
        if not self._skill_manager.is_active:
            return

        # In real integration, we'd get state from simulator sensors.
        # For now, skill_manager handles stepping via skill_step actions.
        # This timer is for autonomous skill execution when state is available.
        pass

    def _publish_metrics(self, metrics: dict):
        """Publish skill metrics to /robopay/metrics topic."""
        msg = String()
        msg.data = json.dumps(metrics)
        self._metrics_pub.publish(msg)
        self.get_logger().debug(f"Published metrics: {metrics}")

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = IsaacSimTron1BridgeNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
