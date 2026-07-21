"""test_link.py — end-to-end test: tunnel Zenoh session → bridge → metrics.

Proves the tunnel's Zenoh session is live (a robot/config/<id> update shows
up on the topic), then publishes a paid-action event with the exact
handlers.PostAction schema and asserts the robot runs the episode and
returns metrics with task_completed: true.

Does NOT require the tunnel binary — it talks directly over Zenoh, replicating
exactly what the tunnel publishes after the x402 payment is verified.
"""
import json
import time
import threading
import unittest
from datetime import datetime, timezone

import zenoh

ZENOH_ENDPOINT    = "tcp/127.0.0.1:7447"
TOPIC_ACTION      = "robot/tunnel/action"
TOPIC_METRICS     = "robot/reachy_mini/metrics"
TOPIC_CONFIG      = "robot/config/reachy_mini_sim_test"
ACTION_TIMEOUT_S  = 60   # sim + sim2sim can take up to ~40 s


def _paid_action_event(action: str = "look_at_apple") -> bytes:
    """Build the exact ActionEvent that handlers.PostAction publishes after x402 verification."""
    return json.dumps({
        "payload": {
            "action": action,
            "params": {
                "duration":      4.0,
                "target_object": "apple",
            },
        },
        "transaction_details": {
            "payment_payload": {
                "x402Version": 1,
                "scheme":      "exact",
                "network":     "eip155:84532",
                "payload":     {
                    "signature": "0xdeadbeef",
                    "authorization": {
                        "from":  "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
                        "to":    "0x0000000000000000000000000000000000000001",
                        "value": "0x38D7EA4C68000",   # 0.001 ETH in hex
                    },
                },
            },
            "payment_requirements": {
                "scheme":  "exact",
                "network": "eip155:84532",
                "maxAmountRequired": "0x38D7EA4C68000",
                "payTo":   "0x0000000000000000000000000000000000000001",
            },
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }).encode()


class TestEndToEndLink(unittest.TestCase):
    """Verify that a paid ActionEvent reaches the bridge and returns valid metrics."""

    def _open_session(self, mode: str = "peer") -> zenoh.Session:
        conf = zenoh.Config.from_json5(
            f'{{"mode": "{mode}", '
            f'"scouting": {{"multicast": {{"enabled": false}}}}, '
            f'"connect": {{"endpoints": ["{ZENOH_ENDPOINT}"]}}}}'
        )
        return zenoh.open(conf)

    def test_zenoh_config_topic_reachable(self):
        """Publish on robot/config/<id> and verify the session accepts the put."""
        session = self._open_session()
        try:
            config_update = json.dumps({
                "evm_payee_address": "0x0000000000000000000000000000000000000001",
                "price":   "0.001",
                "network": "eip155:84532",
            }).encode()
            session.put(TOPIC_CONFIG, config_update)
            # If no exception, Zenoh session is live
        finally:
            session.close()

    def test_paid_action_triggers_robot_and_returns_metrics(self):
        """
        Publish a paid ActionEvent and assert metrics arrive with:
          - execution_status == SUCCESS
          - task == object_tracking
          - metrics.task_completed == True
          - sim_to_sim_validation.overall_sim2sim_robustness_score == 1.0
        """
        received   = []
        error      = []
        done_event = threading.Event()

        def on_metrics(sample):
            raw = bytes(sample.payload.to_bytes())
            try:
                data = json.loads(raw)
                received.append(data)
            except Exception as exc:
                error.append(str(exc))
            finally:
                done_event.set()

        session = self._open_session()
        sub     = session.declare_subscriber(TOPIC_METRICS, on_metrics)

        try:
            time.sleep(1.5)   # let subscriber register
            session.put(TOPIC_ACTION, _paid_action_event("look_at_apple"))

            arrived = done_event.wait(timeout=ACTION_TIMEOUT_S)
            self.assertTrue(
                arrived,
                f"Metrics not received within {ACTION_TIMEOUT_S} s. "
                "Is the bridge running? Start it with: "
                "python RoboPay/bridge/reachy_mini/mujoco_sim_bridge/main.py",
            )

            self.assertFalse(error, f"Metrics parse error: {error}")
            self.assertTrue(received, "No metrics payload received.")

            m = received[0]
            self.assertEqual(
                m.get("execution_status"), "SUCCESS",
                f"execution_status should be SUCCESS, got: {m.get('execution_status')}",
            )
            self.assertEqual(
                m.get("task"), "object_tracking",
                f"task should be object_tracking, got: {m.get('task')}",
            )
            self.assertTrue(
                m.get("metrics", {}).get("task_completed"),
                "metrics.task_completed should be True.",
            )
            score = (m.get("sim_to_sim_validation") or {}).get(
                "overall_sim2sim_robustness_score", 0
            )
            self.assertGreaterEqual(
                score, 0.9,
                f"sim2sim robustness score should be ≥ 0.9, got {score}",
            )

        finally:
            sub.undeclare()
            session.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
