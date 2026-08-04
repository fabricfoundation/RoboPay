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
from dataclasses import dataclass

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
from reachy_mini.action_contract import PROFILE_ID, ActionContractError, validate_action_event

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
DEFAULT_ZENOH_ENDPOINT = "tcp/127.0.0.1:7447"
# Match the checked-in Reachy profile. Deployments may override it, but the
# clean-checkout bridge and profile must agree without an accidental mismatch.
DEFAULT_ROBOT_ID = "reachy-mini-kauker"


@dataclass(frozen=True)
class BridgeSettings:
    """Deployment settings, all overridable without changing source files."""

    robot_id: str
    zenoh_endpoint: str
    action_topic: str
    metrics_topic: str
    result_topic: str
    zenoh_config_path: str | None

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        return cls(
            robot_id=os.environ.get("ROBOT_ID", DEFAULT_ROBOT_ID).strip() or DEFAULT_ROBOT_ID,
            zenoh_endpoint=(
                os.environ.get("ZENOH_ENDPOINT", DEFAULT_ZENOH_ENDPOINT).strip()
                or DEFAULT_ZENOH_ENDPOINT
            ),
            action_topic=(
                os.environ.get("ZENOH_ACTION_TOPIC", ZENOH_TOPIC_ACTION).strip()
                or ZENOH_TOPIC_ACTION
            ),
            metrics_topic=(
                os.environ.get("ZENOH_METRICS_TOPIC", ZENOH_TOPIC_METRICS).strip()
                or ZENOH_TOPIC_METRICS
            ),
            result_topic=(
                os.environ.get("ZENOH_RESULT_TOPIC", ZENOH_TOPIC_RESULT).strip()
                or ZENOH_TOPIC_RESULT
            ),
            zenoh_config_path=os.environ.get("ZENOH_CONFIG") or None,
        )


class ReachyMiniBridgeNode(ROSNode):
    """Zenoh subscriber node for Reachy Mini MuJoCo simulation bridge.
    
    Inherits from rclpy.node.Node when ROS2 is installed, or functions as a
    standalone Zenoh subscriber node when running outside ROS2 environment.
    """

    def __init__(
        self,
        zenoh_listen: str | None = None,
        settings: BridgeSettings | None = None,
    ):
        if HAS_ROS2:
            try:
                rclpy.init()
            except Exception:
                pass
            super().__init__("mujoco_sim_bridge_reachy_mini")

        self.settings = settings or BridgeSettings.from_env()
        self.robot_id = self.settings.robot_id
        self.action_topic = self.settings.action_topic
        self.metrics_topic = self.settings.metrics_topic
        self.result_topic = self.settings.result_topic

        self._mapper  = ReachyMapper()
        self._env     = ReachyMiniEnvironment()
        self._policy  = ReachyTaskPolicy()
        self._metrics = SimulationMetricsTracker()

        # A full JSON5 configuration takes precedence. The endpoint override
        # preserves the previous auto listen/connect behavior for simple local
        # deployments while making the address configurable.
        if self.settings.zenoh_config_path:
            self._session = zenoh.open(
                zenoh.Config.from_file(self.settings.zenoh_config_path)
            )
        else:
            endpoint = zenoh_listen or self.settings.zenoh_endpoint
            try:
                conf = zenoh.Config.from_json5(
                    f'{{"mode": "peer", "scouting": {{"multicast": {{"enabled": false}}}}, "listen": {{"endpoints": ["{endpoint}"]}}}}'
                )
                self._session = zenoh.open(conf)
            except Exception:
                conf = zenoh.Config.from_json5(
                    f'{{"mode": "peer", "scouting": {{"multicast": {{"enabled": false}}}}, "connect": {{"endpoints": ["{endpoint}"]}}}}'
                )
                self._session = zenoh.open(conf)

        # Subscribe to action topic
        self._sub = self._session.declare_subscriber(
            self.action_topic, self._on_action
        )

        # Keep the legacy telemetry topic, and publish the reviewer-facing
        # correlated result contract as well.
        self._pub = self._session.declare_publisher(self.metrics_topic)
        self._result_pub = self._session.declare_publisher(self.result_topic)

        self._log_info(
            f"Bridge node ready for robot '{self.robot_id}'. Listening on: {self.action_topic}"
        )
        self._log_info(f"Metrics will be published to: {self.metrics_topic}")
        self._log_info(f"Correlated results will be published to: {self.result_topic}")

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

    def _publish_result(self, event, result: dict) -> None:
        """Publish a terminal result with the exact paid-action correlation."""
        # validate_action_event has already bound the event to this bridge. Do
        # not fill in missing values or substitute local identity here: doing
        # so would let a malformed paid action look correlated to the Tunnel.
        result["robot_id"] = event.robot_id
        result["profile_id"] = PROFILE_ID
        payload = json.dumps(result).encode()
        self._pub.put(payload)
        self._log_info(f"Metrics published to '{self.metrics_topic}'")
        result_event = {
            "action_id": event.action_id,
            "robot_id": event.robot_id,
            "skill_id": event.skill_id,
            "params_hash": event.params_hash,
            "idempotency_key": event.idempotency_key,
            "status": "success" if result.get("execution_status") == "SUCCESS" else "failure",
            "execution_status": result.get("execution_status", "FAILED"),
            "profile_id": PROFILE_ID,
            "result": result,
        }
        self._result_pub.put(json.dumps(result_event).encode())
        self._log_info(f"Correlated result published to '{self.result_topic}'")

    def _on_action(self, sample):
        """Callback triggered when tunnel publishes an ActionEvent via Zenoh."""
        raw   = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)

        if event is None:
            self._log_error("Failed to parse ActionEvent payload.")
            return

        # Shared Zenoh routers can carry actions for multiple robots.  Never
        # publish a terminal response for a foreign robot: it could be
        # mistaken for that robot's correlated result and prevent its own
        # Tunnel from settling correctly.  Foreign events are dropped before
        # any bridge-side validation or simulator interaction.
        if event.robot_id != self.robot_id:
            self._log_info(f"Ignoring ActionEvent for foreign robot '{event.robot_id}'.")
            return

        try:
            skill_id = validate_action_event(event, self.robot_id)
        except ActionContractError as error:
            # The event was parseable and therefore has a correlation tuple;
            # publish a terminal rejection without resetting or controlling the
            # simulator, so the Tunnel can prove non-settlement.
            self._log_error(f"Rejected action contract: {error}")
            self._publish_result(
                event,
                {
                    "correlation_id": event.action_id,
                    "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
                    "simulator": "MuJoCo",
                    "task": None,
                    "execution_status": "REJECTED",
                    "error_code": error.code,
                    "task_completed": False,
                },
            )
            return

        self._log_info(f"ActionEvent received — action='{event.action}' params={event.params}")

        task = self._mapper.map(event)

        # The action contract has already made this a canonical registered
        # skill. Its top-level action_id is the only correlation source.
        correlation_id = event.action_id

        if task is None:
            # Fail closed: an action outside the registered skill set is
            # rejected without touching the simulator, and the correlated
            # failure result keeps the tunnel from settling the payment.
            self._log_error(f"Rejected unregistered action '{event.action}' — no simulation executed.")
            result = {
                "correlation_id": correlation_id,
                "robot_id": event.robot_id,
                "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
                "simulator": "MuJoCo",
                "task": None,
                "execution_status": "REJECTED",
                "error_code": "UNREGISTERED_ACTION",
                "task_completed": False,
            }
        elif task == "safe_stop":
            # "stop" is a safety action: zero all controls and confirm the
            # halt. It must never start object tracking or any other task.
            self._log_info("Safe stop requested — halting actuation without starting any task.")
            result = self._run_safe_stop(correlation_id)
        elif task == "multi_object_inspection":
            # Multi-target paid missions run the same closed-loop policy once
            # per requested object and return an aggregate inspection result.
            self._log_info(f"Mapped to task: '{task}' — starting MuJoCo execution...")
            result = self._run_inspect_table(event.params, correlation_id)
        else:
            self._log_info(f"Mapped to task: '{task}' — starting MuJoCo execution...")
            result = self._run_simulation(
                task,
                event.params,
                target_object=event.params["target_object"],
                correlation_id=correlation_id,
            )

        self._publish_result(event, result)
        self._log_info(f"Result: {json.dumps(result, indent=2)}")

    def _run_safe_stop(self, correlation_id: str | None) -> dict:
        """Halt the robot safely: zero all actuation without running a task.

        A paid "stop" succeeds by stopping — it must never be remapped to
        object tracking or any other motion task.
        """
        try:
            self._env.set_control([0.0] * int(self._env.num_actuators))
            obs = self._env.step(steps=1)
            sim_time = float(obs.get("sim_time", 0.0))
        except Exception as error:
            self._log_error(f"Safe stop failed: {error}")
            return {
                "correlation_id": correlation_id,
                "robot_id": self.robot_id,
                "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
                "simulator": "MuJoCo",
                "task": "safe_stop",
                "execution_status": "FAILED",
                "error_code": "SAFE_STOP_FAILED",
                "stopped": False,
                "steps_executed": 0,
                "metrics": {"task_completed": False, "stopped": False},
            }
        return {
            "correlation_id": correlation_id,
            "robot_id": self.robot_id,
            "robot_model": "Hugging Face Reachy Mini (Official MJCF)",
            "simulator": "MuJoCo",
            "task": "safe_stop",
            "execution_status": "SUCCESS",
            "stopped": True,
            "sim_duration_seconds": round(sim_time, 2),
            "steps_executed": 0,
            "metrics": {"task_completed": True, "stopped": True},
        }

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
            "robot_id":              self.robot_id,
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
                "robot_id": self.robot_id,
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
            "robot_id": self.robot_id,
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
