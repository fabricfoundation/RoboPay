import json
import rclpy
import zenoh
from rclpy.node import Node
from geometry_msgs.msg import Twist
from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import X2Mapper
from .simulator import X2Simulator
from .result import ReplayGuard, result

class MuJoCoX2BridgeNode(Node):
    def __init__(self):
        super().__init__("mujoco_bridge_agibot_x2")
        self.declare_parameter("zenoh_topic", "robot/tunnel/action"); self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel"); self.declare_parameter("model_path", "")
        self.declare_parameter("result_topic", "robot/tunnel/result"); self.declare_parameter("robot_id", "agibot-x2-sim-001")
        p = self.get_parameter; self._mapper = X2Mapper(); self._pub = self.create_publisher(Twist, p("cmd_vel_topic").value, 10)
        self._sim = X2Simulator(p("model_path").value) if p("model_path").value else None
        self._robot_id = p("robot_id").value; self._result_topic = p("result_topic").value; self._replays = ReplayGuard()
        self._result_session = zenoh.open(zenoh.Config.from_json5(f'{{"listen":{{"endpoints":["{p("zenoh_listen").value}"]}}}}'))
        self._result_pub = self._result_session.declare_publisher(self._result_topic)
        self._zenoh = ZenohSubscriberHelper(p("zenoh_listen").value); self._zenoh.subscribe(p("zenoh_topic").value, self._on_action)
    def _on_action(self, sample):
        try: raw = json.loads(bytes(sample.payload.to_bytes()))
        except (json.JSONDecodeError, UnicodeDecodeError): self.get_logger().warning("Ignored malformed JSON event"); return
        payload, tx = raw.get("payload", {}), raw.get("transaction_details", {})
        action_id, key = payload.get("actionId", ""), payload.get("idempotencyKey", "")
        if not action_id or payload.get("robotId") != self._robot_id or not tx.get("payment_payload"):
            self._publish_result(result(action_id, self._robot_id, key, "FAILED", {}, "missing payment or correlation fields")); return
        if not self._replays.claim(key):
            self._publish_result(result(action_id, self._robot_id, key, "REPLAY_REJECTED", {}, "duplicate idempotency key")); return
        event = parse_action_event(json.dumps(raw).encode())
        try:
            self._pub.publish(self._mapper.map(event)); metrics = self._sim.execute(event.action, float(event.params.get("duration", 1))) if self._sim else {"mode": "cmd_vel"}
            self._publish_result(result(action_id, self._robot_id, key, "SUCCESS", metrics))
        except Exception as exc:
            self._replays.discard(key); self._publish_result(result(action_id, self._robot_id, key, "FAILED", {}, str(exc)))
    def _publish_result(self, value): self._result_pub.put(value.to_json())
    def destroy_node(self): self._zenoh.close(); self._result_session.close(); super().destroy_node()
def main(args=None):
    rclpy.init(args=args); node = MuJoCoX2BridgeNode()
    try: rclpy.spin(node)
    finally: node.destroy_node(); rclpy.shutdown()
