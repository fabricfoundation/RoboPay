"""Reachy Mini MuJoCo bridge node — listens to Zenoh robot/tunnel/action,
triggers the task policy, and publishes metrics back on robot/reachy_mini/metrics.

Mirrors the pattern of bridge/unitree/g1/isaac_sim_bridge/g1/node.py but:
  - Uses standalone eclipse-zenoh (no ROS2 required)
  - Drives a MuJoCo simulation instead of /cmd_vel
"""
import json
import logging
import sys
import os

import zenoh

# Allow imports from src/ subdirectory
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src"))

from simulation.environment import ReachyMiniEnvironment
from simulation.metrics     import SimulationMetricsTracker
from simulation.sim2sim     import Sim2SimValidator
from policy.controller      import ReachyTaskPolicy
from reachy_mini.mapper     import ReachyMapper

# Re-use the shared action event parser from common/zenoh_bridge
import importlib.util as _ilu
_AE_FILE = os.path.normpath(os.path.join(
    _HERE, "..", "..", "..", "common", "zenoh_bridge", "zenoh_bridge", "action_event.py"
))
_ae_spec = _ilu.spec_from_file_location("action_event", _AE_FILE)
_ae_mod  = _ilu.module_from_spec(_ae_spec)
_ae_spec.loader.exec_module(_ae_mod)
parse_action_event = _ae_mod.parse_action_event

logger = logging.getLogger("ReachyMiniBridgeNode")


ZENOH_TOPIC_ACTION  = "robot/tunnel/action"
ZENOH_TOPIC_METRICS = "robot/reachy_mini/metrics"


class ReachyMiniBridgeNode:
    """Zenoh subscriber node for Reachy Mini MuJoCo simulation bridge."""

    def __init__(self, zenoh_listen: str = "tcp/127.0.0.1:7447"):
        self._mapper  = ReachyMapper()
        self._env     = ReachyMiniEnvironment()
        self._policy  = ReachyTaskPolicy()
        self._metrics = SimulationMetricsTracker()

        # Open Zenoh session
        conf = zenoh.Config.from_json5(
            f'{{"listen":{{"endpoints":["{zenoh_listen}"]}}}}'
        )
        self._session = zenoh.open(conf)

        # Subscribe to action topic
        self._sub = self._session.declare_subscriber(
            ZENOH_TOPIC_ACTION, self._on_action
        )

        # Publisher for metrics
        self._pub = self._session.declare_publisher(ZENOH_TOPIC_METRICS)

        logger.info(f"Bridge node ready. Listening on Zenoh topic: {ZENOH_TOPIC_ACTION}")
        logger.info(f"Metrics will be published to: {ZENOH_TOPIC_METRICS}")

    def _on_action(self, sample):
        """Callback triggered when tunnel publishes an ActionEvent via Zenoh."""
        raw   = bytes(sample.payload.to_bytes())
        event = parse_action_event(raw)

        if event is None:
            logger.error("Failed to parse ActionEvent payload.")
            return

        logger.info(f"ActionEvent received — action='{event.action}' params={event.params}")

        task = self._mapper.map(event)
        logger.info(f"Mapped to task: '{task}' — starting MuJoCo execution...")

        # Determine target object from params (default: apple)
        target_object = event.params.get("target_object", "apple")
        result = self._run_simulation(task, event.params, target_object=target_object)

        # Publish metrics back over Zenoh
        payload = json.dumps(result).encode()
        self._pub.put(payload)
        logger.info(f"Metrics published to '{ZENOH_TOPIC_METRICS}'")
        logger.info(f"Result: {json.dumps(result, indent=2)}")

    def _run_simulation(
        self, task: str, params: dict, target_object: str = "apple"
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
                logger.info(f"Task complete at t={obs['sim_time']:.2f}s  phase={phase}")
                break

        final_metrics = self._metrics.get_summary()

        # Sim-to-Sim validation with alternating target objects
        validator = Sim2SimValidator(
            ReachyMiniEnvironment, ReachyTaskPolicy, SimulationMetricsTracker
        )
        sim2sim = validator.run_validation(num_runs=3)

        return {
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

    def spin(self):
        """Block and wait for Zenoh action events."""
        logger.info("Spinning — waiting for ActionEvents from Fabric tunnel...")
        try:
            import time
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Shutting down bridge node.")
        finally:
            self._sub.undeclare()
            self._pub.undeclare()
            self._session.close()


def main(args=None):
    try:
        import rclpy
        rclpy.init(args=args)
    except Exception:
        pass

    bridge = ReachyMiniBridgeNode()
    bridge.spin()


if __name__ == "__main__":
    main()
