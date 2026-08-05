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
DOOR_OPEN_THRESHOLD = 0.05
STEP_FRACTION = 0.1


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
            forward_speed=p("forward_speed").get_parameter_value().double_value,
            backward_speed=p("backward_speed").get_parameter_value().double_value,
            turn_linear_speed=p("turn_linear_speed").get_parameter_value().double_value,
            turn_angular_speed=p("turn_angular_speed").get_parameter_value().double_value,
        )

        self._pub = self.create_publisher(Twist, cmd_vel_topic, 10)
        self._arm_pub = self.create_publisher(JointTrajectory, arm_trajectory_topic, 10)
        self._result_pub = self.create_publisher(JointTrajectory, "robot/tunnel/result", 10)

        self.get_logger().info(f"Adapter started, publishing to {cmd_vel_topic}")
        self.get_logger().info(f"Arm trajectory publishing to {arm_trajectory_topic}")

        self._processed_actions = set()
        self._current_position = [0.0] * len(RIGHT_ARM_JOINTS)
        self._stop_requested = False

        self._zenoh = ZenohSubscriberHelper(zenoh_listen)
        self._zenoh.subscribe(zenoh_topic, self._on_action)
        self.get_logger().info(f"Subscribed to Zenoh topic: {zenoh_topic}")

    def _on_action(self, sample):
        raw = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)
        if event is None:
            self.get_logger().error("Failed to parse action event")
            return

        action_id = getattr(event, "action_id", None)
        if action_id is not None and action_id in self._processed_actions:
            self.get_logger().info(f"Ignoring duplicate action: {action_id}")
            self._publish_result(action_id, "error", "DUPLICATE_ACTION", "Action already processed")
            return

        self.get_logger().info(f"Received action={event.action} params={event.params}")

        if event.action == "stop" or event.action == "cancel":
            self._stop_requested = True
            self.get_logger().info("Stop requested, halting motion")
            if action_id is not None:
                self._processed_actions.add(action_id)
            self._publish_result(action_id, "success", None, "Motion stopped")
            return

        self._stop_requested = False

        if event.action in ("open_door", "open_door_handle"):
            if action_id is not None:
                self._processed_actions.add(action_id)
            self._run_door_open_policy(action_id)
            return

        twist = self._mapper.map(event)
        self._pub.publish(twist)
        if action_id is not None:
            self._processed_actions.add(action_id)
        self.get_logger().info(
            f"Published /cmd_vel: linear.x={twist.linear.x:.2f} angular.z={twist.angular.z:.2f}"
        )
        self._publish_result(action_id, "success", None, "Velocity command published")

    def _run_door_open_policy(self, action_id):
        position = list(self._current_position)
        max_steps = 200
        step = 0

        while step < max_steps:
            if self._stop_requested:
                self.get_logger().info("Door-open policy halted by stop request")
                self._publish_result(action_id, "error", "STOPPED", "Motion halted before completion")
                return

            done = True
            for i, target in enumerate(DOOR_OPEN_TARGET_ANGLES):
                diff = target - position[i]
                if abs(diff) > DOOR_OPEN_THRESHOLD:
                    position[i] += diff * STEP_FRACTION
                    done = False

            self._current_position = position
            self._publish_arm_trajectory(position)

            if done:
                break
            step += 1

        final_diffs = [
            abs(DOOR_OPEN_TARGET_ANGLES[i] - position[i]) for i in range(len(position))
        ]
        success = all(d <= DOOR_OPEN_THRESHOLD for d in final_diffs)

        if success:
            self.get_logger().info(f"Door-open policy completed in {step} steps, final position={position}")
            self._publish_result(action_id, "success", None, f"Door opened, final_position={position}")
        else:
            self.get_logger().error(f"Door-open policy did not converge, final position={position}")
            self._publish_result(action_id, "error", "NOT_CONVERGED", f"Final position={position}")

    def _publish_arm_trajectory(self, position):
        traj = JointTrajectory()
        traj.joint_names = RIGHT_ARM_JOINTS
        point = JointTrajectoryPoint()
        point.positions = position
        point.time_from_start.sec = 0
        traj.points = [point]
        self._arm_pub.publish(traj)

    def _publish_result(self, action_id, status, error_code, message):
        result = JointTrajectory()
        result.joint_names = ["result"]
        point = JointTrajectoryPoint()
        point.positions = [1.0 if status == "success" else 0.0]
        result.points = [point]
        self._result_pub.publish(result)
        self.get_logger().info(
            f"Result published: actionId={action_id} status={status} error={error_code} message={message}"
        )

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = IsaacSimG1BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
