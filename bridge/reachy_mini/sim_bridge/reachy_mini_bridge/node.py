"""ROS2 node for Fabric -> Reachy Mini gaze-tracking adapter.

Subscribes to the Zenoh action topic (Fabric tunnel output), maps actions
to gaze commands, drives the ReachyGazePolicy against a live MuJoCo
environment, and republishes per-step metrics for observability.

Architecture: the MuJoCo env owns all geometry/IK (look_at_target,
angular_error_to); the policy is a pure reactive FSM that only consumes
(target_visible, angular_error_rad) and reports state/locked -- it does
not produce joint angles. This matches src/policy/controller.py and
src/simulation/mujoco_env.py.
"""
import json
import sys
import os

import rclpy
from rclpy.node import Node

from zenoh_bridge import parse_action_event, ZenohSubscriberHelper
from .mapper import ReachyMiniMapper

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_here, "..", "src"))
from policy.controller import GazePolicyConfig, ReachyGazePolicy  # noqa: E402
from simulation.mujoco_env import ReachyMiniMujocoEnv  # noqa: E402
from simulation.metrics import EpisodeMetrics  # noqa: E402


class SimBridgeReachyMiniNode(Node):
    def __init__(self):
        super().__init__("sim_bridge_reachy_mini")

        self.declare_parameter("zenoh_topic", "robot/tunnel/action")
        self.declare_parameter("zenoh_listen", "tcp/127.0.0.1:7447")
        self.declare_parameter("metrics_topic", "robot/reachy_mini/metrics")
        self.declare_parameter("lock_tolerance_rad", 0.30)
        self.declare_parameter("lock_hold_steps", 15)

        p = self.get_parameter
        cfg = GazePolicyConfig(
            lock_tolerance_rad=p("lock_tolerance_rad").get_parameter_value().double_value,
            lock_hold_steps=p("lock_hold_steps").get_parameter_value().integer_value,
        )
        self._policy = ReachyGazePolicy(cfg)
        self._mapper = ReachyMiniMapper()

        try:
            self._env = ReachyMiniMujocoEnv()
            self.get_logger().info(f"Loaded MuJoCo scene: {self._env.scene_path}")
        except FileNotFoundError as e:
            self._env = None
            self.get_logger().error(str(e))

        self._metrics: EpisodeMetrics = None
        self._current_target = None

        zenoh_topic = p("zenoh_topic").get_parameter_value().string_value
        zenoh_listen = p("zenoh_listen").get_parameter_value().string_value
        self._zenoh = ZenohSubscriberHelper(zenoh_listen)
        self._zenoh.subscribe(zenoh_topic, self._on_action)
        self.get_logger().info(f"Subscribed to Zenoh topic: {zenoh_topic}")

        self._metrics_topic = p("metrics_topic").get_parameter_value().string_value

        self.create_timer(1.0 / 30.0, self._on_tick)

    def _on_action(self, sample):
        raw = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)
        if event is None:
            self.get_logger().error("Failed to parse action event")
            return
        cmd = self._mapper.map(event)
        self.get_logger().info(f"Gaze command: mode={cmd.mode} target={cmd.target_name}")

        if cmd.mode == "reset":
            self._policy.reset()
            self._current_target = None
            self._metrics = None
        elif cmd.mode == "track":
            self._current_target = cmd.target_name
            self._metrics = EpisodeMetrics(
                robot_id="reachy_mini_sim", simulator="MuJoCo",
                target_name=cmd.target_name or "unnamed",
            )
        # "hold" leaves state untouched.

    def _on_tick(self):
        if self._env is None:
            return

        visible = False
        yaw_err = pitch_err = None
        angular_error_rad = None

        if self._current_target is not None:
            target_pos = self._env.get_target_world_pos(self._current_target)
            if target_pos is not None:
                self._env.look_at_target(target_pos)
            yaw_err, pitch_err, visible = self._env.angular_error_to(self._current_target)
            if visible:
                angular_error_rad = (yaw_err ** 2 + pitch_err ** 2) ** 0.5

        out = self._policy.step(target_visible=visible, angular_error_rad=angular_error_rad)
        self._env.step()

        if self._metrics is not None:
            self._metrics.log(yaw_err, pitch_err, 0.0,
                                angular_error_rad, visible, out.state)
            if out.locked or len(self._metrics.records) % 30 == 0:
                self._zenoh.publish(self._metrics_topic, json.dumps(self._metrics.summary()).encode())

    def destroy_node(self):
        self._zenoh.close()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = SimBridgeReachyMiniNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
