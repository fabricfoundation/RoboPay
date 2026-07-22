"""Reachy Mini MuJoCo bridge node — listens to Zenoh robot/tunnel/action,
triggers the task policy, and publishes correlated results on robot/tunnel/result.

Mirrors the pattern of bridge/unitree/g1/isaac_sim_bridge/g1/node.py but:
  - Uses standalone eclipse-zenoh (no ROS2 required)
  - Drives a MuJoCo simulation instead of /cmd_vel
"""
import json
import logging
import sys
import os

import zenoh

_HERE = os.path.dirname(os.path.abspath(__file__))
_pkg_dir = os.path.normpath(os.path.join(_HERE, ".."))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from simulation.environment import ReachyMiniEnvironment
from simulation.metrics     import SimulationMetricsTracker
from simulation.sim2sim     import Sim2SimValidator
from policy.controller      import ReachyTaskPolicy
from reachy_mini.mapper     import ReachyMapper

# Re-use the shared action event parser from common/zenoh_bridge
try:
    from zenoh_bridge.action_event import parse_action_event
except ImportError:
    import importlib.util as _ilu
    _AE_FILE = os.path.normpath(os.path.join(
        _HERE, "..", "..", "..", "common", "zenoh_bridge", "zenoh_bridge", "action_event.py"
    ))
    _ae_spec = _ilu.spec_from_file_location("action_event", _AE_FILE)
    _ae_mod  = _ilu.module_from_spec(_ae_spec)
    _ae_spec.loader.exec_module(_ae_mod)
    parse_action_event = _ae_mod.parse_action_event

try:
    import rclpy
    from rclpy.node import Node as ROSNode
    HAS_ROS2 = True
except ImportError:
    HAS_ROS2 = False
    ROSNode = object

logger = logging.getLogger("ReachyMiniBridgeNode")


ZENOH_TOPIC_ACTION  = "robot/tunnel/action"
ZENOH_TOPIC_METRICS = "robot/reachy_mini/metrics"
ZENOH_TOPIC_RESULT  = "robot/tunnel/result"


class ReachyMiniBridgeNode(ROSNode):
    """Zenoh subscriber node for Reachy Mini MuJoCo simulation bridge.
    
    Inherits from rclpy.node.Node when ROS2 is installed, or functions as a
    standalone Zenoh subscriber node when running outside ROS2 environment.
    """

    def __init__(self, zenoh_listen: str = "tcp/127.0.0.1:7447"):
        if HAS_ROS2:
            try:
                rclpy.init()
            except Exception:
                pass
            super().__init__("mujoco_sim_bridge_reachy_mini")

        self._mapper  = ReachyMapper()
        self._env     = ReachyMiniEnvironment()
        self._policy  = ReachyTaskPolicy()
        self._metrics = SimulationMetricsTracker()

        # Open Zenoh session with fast peer mode and multicast scouting disabled for instant cross-platform startup
        try:
            conf = zenoh.Config.from_json5(
                f'{{"mode": "peer", "scouting": {{"multicast": {{"enabled": false}}}}, "listen": {{"endpoints": ["{zenoh_listen}"]}}}}'
            )
            self._session = zenoh.open(conf)
        except Exception:
            conf = zenoh.Config.from_json5(
                f'{{"mode": "peer", "scouting": {{"multicast": {{"enabled": false}}}}, "connect": {{"endpoints": ["{zenoh_listen}"]}}}}'
            )
            self._session = zenoh.open(conf)

        # Subscribe to action topic
        self._sub = self._session.declare_subscriber(
            ZENOH_TOPIC_ACTION, self._on_action
        )

        # Keep the legacy telemetry topic, and publish the reviewer-facing
        # correlated result contract as well.
        self._pub = self._session.declare_publisher(ZENOH_TOPIC_METRICS)
        self._result_pub = self._session.declare_publisher(ZENOH_TOPIC_RESULT)

        self._log_info(f"Bridge node ready. Listening on Zenoh topic: {ZENOH_TOPIC_ACTION}")
        self._log_info(f"Metrics will be published to: {ZENOH_TOPIC_METRICS}")
        self._log_info(f"Correlated results will be published to: {ZENOH_TOPIC_RESULT}")

    def _log_info(self, msg: str):
        if HAS_ROS2 and hasattr(self, "get_logger"):
            self.get_logger().info(msg)
        else:
            logger.info(msg)

    def _log_error(self, msg: str):
        if HAS_ROS2 and hasattr(self, "get_logger"):
            self.get_logger().error(msg)
        else:
            logger.error(msg)

    def _on_action(self, sample):
        """Callback triggered when tunnel publishes an ActionEvent via Zenoh."""
        raw   = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)

        if event is None:
            self._log_error("Failed to parse ActionEvent payload.")
            return

        self._log_info(f"ActionEvent received — action='{event.action}' params={event.params}")

        task = self._mapper.map(event)
        self._log_info(f"Mapped to task: '{task}' — starting MuJoCo execution...")

        # Preserve the public request id across the complete mission.
        correlation_id = (
            event.params.get("request_id")
            or event.params.get("correlation_id")
            or event.action_id
        )

        # Multi-target paid missions run the same closed-loop policy once per
        # requested object and return an aggregate inspection result.
        if task == "multi_object_inspection":
            result = self._run_inspect_table(event.params, correlation_id)
        else:
            result = self._run_simulation(
                task,
                event.params,
                target_object=event.params.get("target_object", "apple"),
                correlation_id=correlation_id,
            )

        # Publish the existing metrics payload for compatibility.
        payload = json.dumps(result).encode()
        self._pub.put(payload)
        self._log_info(f"Metrics published to '{ZENOH_TOPIC_METRICS}'")

        # Publish the explicit tunnel result envelope.  The action_id is the
        # end-to-end correlation key generated/forwarded by the Tunnel.
        result_event = {
            "action_id": event.action_id or correlation_id or "",
            "robot_id": event.robot_id or result.get("robot_id", ""),
            "skill_id": event.skill_id or event.action,
            "params_hash": event.params_hash,
            "idempotency_key": event.idempotency_key,
            "status": "success" if result.get("execution_status") == "SUCCESS" else "failure",
            "execution_status": result.get("execution_status", "FAILED"),
            "result": result,
        }
        self._result_pub.put(json.dumps(result_event).encode())
        self._log_info(f"Correlated result published to '{ZENOH_TOPIC_RESULT}'")
        self._log_info(f"Result: {json.dumps(result, indent=2)}")

    def _run_simulation(
        self,
        task: str,
        params: dict,
        target_object: str = "apple",
        correlation_id: str | None = None,
        validate_sim2sim: bool = True,
    ) -> dict:
        """Run the MuJoCo simulation loop with the policy and return metrics."""
        obs = self._env.reset(target_object=target_object)
        self._policy.reset()
        self._metrics.reset(obs)

        step_count    = 0
        phase_history = []
        last_summary  = {}
        max_sim_time  = float(params.get("duration", 8.0))

        while obs["sim_time"] < max_sim_time:
            action, phase = self._policy.compute_action(obs, last_summary)
            phase_history.append(phase)
            self._env.set_control(action)
            obs          = self._env.step(steps=5)
            last_summary = self._metrics.update(obs)
            step_count  += 1

            if last_summary["task_completed"]:
                self._log_info(f"Task complete at t={obs['sim_time']:.2f}s  phase={phase}")
                break

        final_metrics = self._metrics.get_summary()

        sim2sim = {}
        if validate_sim2sim:
            validator = Sim2SimValidator(
                ReachyMiniEnvironment, ReachyTaskPolicy, SimulationMetricsTracker
            )
            sim2sim = validator.run_validation(num_runs=3)

        return {
            "correlation_id": correlation_id,
            "robot_id":              "reachy_mini_sim_01",
            "robot_model":           "Hugging Face Reachy Mini (Official MJCF)",
            "simulator":             "MuJoCo",
            "task":                  task,
            "execution_status":      (
                "SUCCESS" if final_metrics["task_completed"] else "COMPLETED_WITH_METRICS"
            ),
            "sim_duration_seconds":  round(obs["sim_time"], 2),
            "steps_executed":        step_count,
            "phases_visited":        sorted(set(phase_history)),
            "metrics":               final_metrics,
            "sim_to_sim_validation": sim2sim,
        }

    def _run_inspect_table(self, params: dict, correlation_id: str | None) -> dict:
        """Inspect a requested sequence of table objects with live physics.

        Each target gets an independent search/tracking episode. The policy
        reads simulator state from scratch for every target; no trajectory is
        reused. The mission succeeds only when every requested target is
        completed.
        """
        requested = params.get("targets", ["apple", "croissant", "duck"])
        if not isinstance(requested, list):
            requested = [requested]
        targets = [str(target).lower() for target in requested if str(target).strip()]
        targets = targets[:5]
        allowed = {"apple", "croissant", "duck"}
        targets = [target for target in targets if target in allowed]
        if not targets:
            return {
                "correlation_id": correlation_id,
                "robot_id": "reachy_mini_sim_01",
                "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
                "simulator": "MuJoCo",
                "task": "multi_object_inspection",
                "execution_status": "FAILED",
                "error_code": "NO_VALID_TARGETS",
                "objects_requested": 0,
                "objects_completed": 0,
                "task_completed": False,
            }

        per_target_duration = float(params.get("per_target_duration", 4.0))
        per_target_duration = min(max(per_target_duration, 2.0), 8.0)
        episodes = []
        for target in targets:
            episode = self._run_simulation(
                "object_tracking",
                {"duration": per_target_duration},
                target_object=target,
                correlation_id=f"{correlation_id}:{target}" if correlation_id else target,
                validate_sim2sim=False,
            )
            episodes.append({
                "target_object": target,
                "task_completed": bool(episode["metrics"].get("task_completed")),
                "tracking_success_rate": episode["metrics"].get("tracking_success_rate", 0.0),
                "min_tracking_error_rad": episode["metrics"].get("min_tracking_error_rad"),
                "sim_duration_seconds": episode["sim_duration_seconds"],
                "steps_executed": episode["steps_executed"],
            })

        completed = [episode for episode in episodes if episode["task_completed"]]
        validator = Sim2SimValidator(
            ReachyMiniEnvironment, ReachyTaskPolicy, SimulationMetricsTracker
        )
        sim2sim = validator.run_validation(num_runs=3)
        return {
            "correlation_id": correlation_id,
            "robot_id": "reachy_mini_sim_01",
            "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
            "simulator": "MuJoCo",
            "task": "multi_object_inspection",
            "execution_status": "SUCCESS" if len(completed) == len(targets) else "FAILED",
            "objects_requested": len(targets),
            "objects_found": len(completed),
            "objects_completed": len(completed),
            "task_completed": len(completed) == len(targets),
            "tracking_success_rate": round(
                sum(item["tracking_success_rate"] for item in episodes) / len(episodes), 3
            ),
            "metrics": {
                "task_completed": len(completed) == len(targets),
                "tracking_success_rate": round(
                    sum(item["tracking_success_rate"] for item in episodes) / len(episodes), 3
                ),
                "objects_requested": len(targets),
                "objects_completed": len(completed),
            },
            "steps_executed": sum(item["steps_executed"] for item in episodes),
            "sim_duration_seconds": round(
                sum(item["sim_duration_seconds"] for item in episodes), 2
            ),
            "per_target": episodes,
            "sim_to_sim_validation": sim2sim,
        }

    def destroy_node(self):
        """Clean up Zenoh subscriber and publisher resources."""
        try:
            self._sub.undeclare()
            self._pub.undeclare()
            self._result_pub.undeclare()
            self._session.close()
        except Exception:
            pass
        if HAS_ROS2 and hasattr(super(), "destroy_node"):
            super().destroy_node()

    def spin(self):
        """Block and wait for Zenoh action events (standalone mode)."""
        self._log_info("Spinning — waiting for ActionEvents from Fabric tunnel...")
        try:
            import time
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self._log_info("Shutting down bridge node.")
        finally:
            self.destroy_node()


def main(args=None):
    if HAS_ROS2:
        rclpy.init(args=args)
        node = ReachyMiniBridgeNode()
        try:
            rclpy.spin(node)
        except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
            pass
        finally:
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
    else:
        bridge = ReachyMiniBridgeNode()
        bridge.spin()


if __name__ == "__main__":
    main()
