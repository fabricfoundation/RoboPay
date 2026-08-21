"""Unit-level routing tests for the fail-closed Atlas Zenoh bridge.

The real Tunnel/x402/Zenoh/MuJoCo proof lives in the integration tests.  These
fast tests isolate the bridge's second authorization boundary so malformed or
misrouted events cannot regress unnoticed.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from atlas_drc_bridge.bridge import AtlasZenohBridge, _load_event_parser


class _Payload:
    def __init__(self, value: bytes):
        self._value = value

    def to_bytes(self) -> bytes:
        return self._value


class _Sample:
    def __init__(self, document: dict):
        self.payload = _Payload(json.dumps(document).encode("utf-8"))


class _Publisher:
    def __init__(self):
        self.documents: list[dict] = []

    def put(self, payload: bytes) -> None:
        self.documents.append(json.loads(payload))


def _event(action: str, params: dict | None = None, action_id: str = "action-1") -> dict:
    action_params = params if params is not None else {}
    canonical = json.dumps(action_params, separators=(",", ":"), sort_keys=True)
    return {
        "payload": {"skillId": action, "params": action_params},
        "action_id": action_id,
        "robot_id": "atlas-bridge-contract-test",
        "skill_id": action,
        "idempotency_key": action_id,
        "params_hash": "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "params_canonical": canonical,
        "transaction_details": {"payment_requirements": {"network": "eip155:84532"}},
    }


def _bridge(runner) -> AtlasZenohBridge:
    bridge = AtlasZenohBridge.__new__(AtlasZenohBridge)
    bridge.robot_id = "atlas-bridge-contract-test"
    bridge._model_dir = None
    bridge._episode_runner = runner
    bridge._parse_action_event = _load_event_parser()
    bridge._result_publisher = _Publisher()
    bridge._metrics_publisher = _Publisher()
    bridge._stop_event = threading.Event()
    bridge._stop_confirmed = threading.Event()
    bridge._worker_lock = threading.Lock()
    bridge._worker = None
    return bridge


class AtlasBridgeContractTests(unittest.TestCase):
    def test_parser_requires_skill_correlation_and_untampered_params(self) -> None:
        parser = _load_event_parser()
        parsed = parser(
            json.dumps(
                _event("wave_right_arm", {"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 5})
            ).encode("utf-8")
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.action_id, "action-1")

        malformed = _event("wave_right_arm")
        del malformed["idempotency_key"]
        self.assertIsNone(parser(json.dumps(malformed).encode("utf-8")))
        tampered = _event("wave_right_arm", {"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 5})
        tampered["payload"]["params"]["cycles"] = 3
        self.assertIsNone(parser(json.dumps(tampered).encode("utf-8")))

    def test_success_and_failure_keep_tunnel_correlation(self) -> None:
        for simulator_result, expected_status in (
            ({"success": True, "completion_reason": "wave_complete"}, "success"),
            ({"success": False, "completion_reason": "time_limit"}, "failure"),
        ):
            with self.subTest(expected_status=expected_status):
                bridge = _bridge(lambda *_args, **_kwargs: simulator_result)
                bridge._on_action(
                    _Sample(_event("wave_right_arm", {"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 5}))
                )
                bridge._worker.join(timeout=2)
                result = bridge._result_publisher.documents[-1]
                self.assertEqual(result["action_id"], "action-1")
                self.assertEqual(result["robot_id"], bridge.robot_id)
                self.assertEqual(result["skill_id"], "wave_right_arm")
                self.assertEqual(result["idempotency_key"], "action-1")
                self.assertEqual(result["status"], expected_status)

    def test_unknown_invalid_and_wrong_robot_never_run_simulation(self) -> None:
        calls: list[object] = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return {"success": True}

        bridge = _bridge(runner)
        bridge._on_action(_Sample(_event("object_tracking")))
        self.assertEqual(calls, [])
        self.assertEqual(bridge._result_publisher.documents[-1]["result"]["error_code"], "UNREGISTERED_ACTION")

        bridge = _bridge(runner)
        bridge._on_action(_Sample(_event("wave_right_arm", {"cycles": 1, "maxDurationSec": 5, "extra": True})))
        self.assertEqual(calls, [])
        self.assertEqual(bridge._result_publisher.documents[-1]["result"]["error_code"], "INVALID_PARAMS")

        bridge = _bridge(runner)
        wrong_robot = _event("wave_right_arm", {"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 5})
        wrong_robot["robot_id"] = "different-robot"
        bridge._on_action(_Sample(wrong_robot))
        self.assertEqual(calls, [])
        self.assertEqual(bridge._result_publisher.documents, [])

    def test_stop_interrupts_running_wave_without_turning_into_wave(self) -> None:
        started = threading.Event()

        def interrupted_runner(*_args, stop_requested, **_kwargs):
            started.set()
            deadline = time.monotonic() + 2
            while not stop_requested() and time.monotonic() < deadline:
                time.sleep(0.005)
            return {"success": False, "safe_stop_applied": stop_requested(), "completion_reason": "safe_stopped"}

        bridge = _bridge(interrupted_runner)
        bridge._on_action(
            _Sample(_event("wave_right_arm", {"cycles": 1, "amplitudeRad": 0.30, "maxDurationSec": 5}, "wave-1"))
        )
        self.assertTrue(started.wait(timeout=1))
        bridge._on_action(_Sample(_event("stop", {}, "stop-1")))
        bridge._worker.join(timeout=2)

        by_action = {item["action_id"]: item for item in bridge._result_publisher.documents}
        self.assertEqual(by_action["wave-1"]["status"], "failure")
        self.assertTrue(by_action["wave-1"]["result"]["safe_stop_applied"])
        self.assertEqual(by_action["stop-1"]["status"], "success")
        self.assertTrue(by_action["stop-1"]["result"]["safe_stop_applied"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
