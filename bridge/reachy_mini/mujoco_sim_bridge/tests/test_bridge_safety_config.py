import os
import unittest
from unittest.mock import patch

from reachy_mini.node import BridgeSettings, ReachyMiniBridgeNode


class _SafeStopEnvironment:
    num_actuators = 3

    def __init__(self):
        self.control = None

    def set_control(self, control):
        self.control = control

    def step(self, steps=1):
        return {"sim_time": 1.25, "steps": steps}


class _FailingStopEnvironment(_SafeStopEnvironment):
    def set_control(self, control):
        raise RuntimeError("actuator bus unavailable")


def _bare_node(environment):
    node = object.__new__(ReachyMiniBridgeNode)
    node._env = environment
    node.robot_id = "configured-reachy"
    node._log_error = lambda _message: None
    return node


class BridgeSafetyAndConfigTests(unittest.TestCase):
    def test_environment_configures_robot_endpoint_and_topics(self):
        with patch.dict(
            os.environ,
            {
                "ROBOT_ID": "reachy-prod-7",
                "ZENOH_ENDPOINT": "tcp/10.0.0.7:7447",
                "ZENOH_ACTION_TOPIC": "robots/reachy/actions",
                "ZENOH_RESULT_TOPIC": "robots/reachy/results",
                "ZENOH_METRICS_TOPIC": "robots/reachy/metrics",
                "ZENOH_CONFIG": "/etc/robopay/zenoh.json5",
            },
            clear=True,
        ):
            settings = BridgeSettings.from_env()

        self.assertEqual(settings.robot_id, "reachy-prod-7")
        self.assertEqual(settings.zenoh_endpoint, "tcp/10.0.0.7:7447")
        self.assertEqual(settings.action_topic, "robots/reachy/actions")
        self.assertEqual(settings.result_topic, "robots/reachy/results")
        self.assertEqual(settings.metrics_topic, "robots/reachy/metrics")
        self.assertEqual(settings.zenoh_config_path, "/etc/robopay/zenoh.json5")

    def test_safe_stop_zeroes_every_actuator(self):
        environment = _SafeStopEnvironment()
        result = _bare_node(environment)._run_safe_stop("stop-1")
        self.assertEqual(environment.control, [0.0, 0.0, 0.0])
        self.assertEqual(result["execution_status"], "SUCCESS")
        self.assertTrue(result["stopped"])

    def test_safe_stop_failure_is_terminal_failure(self):
        result = _bare_node(_FailingStopEnvironment())._run_safe_stop("stop-2")
        self.assertEqual(result["execution_status"], "FAILED")
        self.assertEqual(result["error_code"], "SAFE_STOP_FAILED")
        self.assertFalse(result["stopped"])
        self.assertFalse(result["metrics"]["task_completed"])


if __name__ == "__main__":
    unittest.main()
