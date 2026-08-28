"""ROS2 node for Fabric → sim-arm-01 (2-DOF planar arm, MuJoCo) adapter.

Subscribes to paid actions on robot/tunnel/action, runs the closed-loop MuJoCo
servo, and publishes an actionId-correlated **terminal result** back on
robot/tunnel/result. The relay/tunnel consumes that result to gate settlement:
success settles, ACTION_FAILED / INVALID_PARAMS does not.

The settlement-gating logic and its success + failure evidence are demonstrated
transport-independently (and in CI) by sim_arm_01.flow; this node is the live
Zenoh runtime of the same flow.
"""
import json

import rclpy
import zenoh
from rclpy.node import Node
from geometry_msgs.msg import Twist

from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import SimArm01Mapper
from .simulator import SimArm01Simulator

ROBOT_ID = "sim-arm-01"
RESULT_TOPIC = "robot/tunnel/result"


def _correlation_id(raw: bytes) -> str:
    """Best-effort actionId for result correlation from the tunnel payload."""
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    tx = event.get("transaction_details") or {}
    return (event.get("actionId") or (event.get("payload") or {}).get("actionId")
            or tx.get("actionId") or tx.get("txHash") or event.get("timestamp", ""))


class MuJoCoSimArm01BridgeNode(Node):
    def __init__(self):
        super().__init__("mujoco_bridge_sim_arm_01")

        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("result_topic", RESULT_TOPIC)
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")

        p = self.get_parameter
        zenoh_topic   = p("zenoh_topic").get_parameter_value().string_value
        zenoh_listen  = p("zenoh_listen").get_parameter_value().string_value
        result_topic  = p("result_topic").get_parameter_value().string_value
        cmd_vel_topic = p("cmd_vel_topic").get_parameter_value().string_value

        self._mapper = SimArm01Mapper()
        self._sim    = SimArm01Simulator()
        self._pub    = self.create_publisher(Twist, cmd_vel_topic, 10)

        # Publisher for the terminal result the tunnel uses to gate settlement.
        self._result_topic = result_topic
        self._zsession = zenoh.open(zenoh.Config())
        self._result_pub = self._zsession.declare_publisher(result_topic)

        self._zenoh = ZenohSubscriberHelper(zenoh_listen)
        self._zenoh.subscribe(zenoh_topic, self._on_action)
        self.get_logger().info(
            f"sim-arm-01 bridge started: {zenoh_topic} -> {result_topic}")

    def _publish_result(self, action_id, status, metrics=None, code="", message=""):
        result = {
            "actionId": action_id, "robotId": ROBOT_ID, "skillId": "move_to_pose",
            "status": status, "metrics": metrics or {}, "code": code, "message": message,
        }
        self._result_pub.put(json.dumps(result))
        self.get_logger().info(f"published result status={status} action={action_id}")

    def _on_action(self, sample):
        raw   = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)
        if event is None:
            self.get_logger().error("Failed to parse action event")
            return

        action_id = _correlation_id(raw)
        self.get_logger().info(f"Received action={event.action} params={event.params}")
        self._pub.publish(self._mapper.map(event))

        if event.action != "move_to_pose":
            return  # unknown action -> no actuation, no settlement

        try:
            target = self._mapper.parse_target(event)
        except (TypeError, ValueError, KeyError):
            self._publish_result(action_id, "error", code="INVALID_PARAMS",
                                  message="target_qpos must be 2 numeric angles")
            return

        metrics = self._sim.execute(target)
        if metrics.get("success"):
            self._publish_result(action_id, "success", metrics=metrics)
        else:
            self._publish_result(action_id, "error", metrics=metrics,
                                 code="ACTION_FAILED",
                                 message="arm did not reach target within step budget")

    def destroy_node(self):
        self._zenoh.close()
        self._zsession.close()
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
