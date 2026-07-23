import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import X2Mapper
from .simulator import X2Simulator

class MuJoCoX2BridgeNode(Node):
    def __init__(self):
        super().__init__("mujoco_bridge_agibot_x2")
        self.declare_parameter("zenoh_topic", "robot/tunnel/action"); self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel"); self.declare_parameter("model_path", "")
        p = self.get_parameter; self._mapper = X2Mapper(); self._pub = self.create_publisher(Twist, p("cmd_vel_topic").value, 10)
        self._sim = X2Simulator(p("model_path").value) if p("model_path").value else None
        self._zenoh = ZenohSubscriberHelper(p("zenoh_listen").value); self._zenoh.subscribe(p("zenoh_topic").value, self._on_action)
    def _on_action(self, sample):
        event = parse_action_event(bytes(sample.payload.to_bytes()))
        if event is None: self.get_logger().warning("Ignored malformed action event"); return
        self._pub.publish(self._mapper.map(event))
        if self._sim:
            try: self.get_logger().info("X2 execution: %s", self._sim.execute(event.action, float(event.params.get("duration", 1))))
            except Exception as exc: self.get_logger().error("Simulation failed: %s", exc)
    def destroy_node(self): self._zenoh.close(); super().destroy_node()
def main(args=None):
    rclpy.init(args=args); node = MuJoCoX2BridgeNode()
    try: rclpy.spin(node)
    finally: node.destroy_node(); rclpy.shutdown()
