import hashlib
import json
import threading
import time
import unittest
from unittest.mock import patch

from k1_inspection_bridge.bridge import K1ZenohBridge, _load_event_parser


class _Payload:
    def __init__(self, value): self.value = value
    def to_bytes(self): return self.value


class _Sample:
    def __init__(self, document): self.payload = _Payload(json.dumps(document).encode())


class _Publisher:
    def __init__(self): self.documents = []
    def put(self, payload): self.documents.append(json.loads(payload))


def event(action="inspect_target_sequence", params=None, action_id="action-1"):
    params = {} if params is None else params
    canonical = json.dumps(params, separators=(",", ":"), sort_keys=True)
    return {
        "payload": {"skillId": action, "params": params}, "action_id": action_id,
        "robot_id": "k1-contract-test", "skill_id": action, "idempotency_key": action_id,
        "params_hash": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
        "params_canonical": canonical, "transaction_details": {"payment_requirements": {"network": "eip155:84532"}},
    }


def bridge():
    item = K1ZenohBridge.__new__(K1ZenohBridge)
    item.robot_id = "k1-contract-test"; item._model_dir = None; item._parse = _load_event_parser()
    item._results = _Publisher(); item._metrics = _Publisher(); item._stop = threading.Event()
    item._stop_applied = threading.Event(); item._lock = threading.Lock(); item._worker = None
    return item


class K1BridgeContractTests(unittest.TestCase):
    def test_parser_preserves_paid_correlation_tuple(self):
        parsed = _load_event_parser()(json.dumps(event()).encode())
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.action, parsed.action_id, parsed.idempotency_key), ("inspect_target_sequence", "action-1", "action-1"))

    def test_success_and_failure_are_correlated(self):
        for simulator_result, status in (({"success": True}, "success"), ({"success": False}, "failure")):
            item = bridge()
            with patch("k1_inspection_bridge.bridge.run_inspection", return_value=simulator_result):
                item._on_action(_Sample(event())); item._worker.join(2)
            result = item._results.documents[-1]
            self.assertEqual(result["status"], status)
            self.assertEqual((result["action_id"], result["robot_id"], result["skill_id"], result["idempotency_key"]), ("action-1", "k1-contract-test", "inspect_target_sequence", "action-1"))

    def test_invalid_contracts_never_run_simulator(self):
        cases = (
            (event("walk_forward"), "UNREGISTERED_ACTION"),
            (event(params={"maxDurationSec": 31}), "INVALID_DURATION"),
            (event(params={"targets": ["left", "left"]}), "INVALID_TARGETS"),
            (event(params={"targets": ["rear"]}), "INVALID_TARGETS"),
            (event(params={"speedScale": 1.1}), "INVALID_SPEED"),
            (event(params={"legacy": True}), "INVALID_PARAMS"),
        )
        for document, code in cases:
            item = bridge()
            with patch("k1_inspection_bridge.bridge.run_inspection") as runner:
                item._on_action(_Sample(document))
            runner.assert_not_called(); self.assertEqual(item._results.documents[-1]["result"]["error_code"], code)

    def test_foreign_robot_is_silent(self):
        item = bridge(); document = event(); document["robot_id"] = "foreign"
        with patch("k1_inspection_bridge.bridge.run_inspection") as runner:
            item._on_action(_Sample(document))
        runner.assert_not_called(); self.assertEqual(item._results.documents, [])

    def test_paid_stop_interrupts_and_confirms_safe_stop(self):
        item = bridge(); started = threading.Event()
        def runner(*_args, stop_requested, **_kwargs):
            started.set()
            while not stop_requested(): time.sleep(0.005)
            return {"success": False, "safe_stop_applied": True, "completion_reason": "safe_stopped"}
        with patch("k1_inspection_bridge.bridge.run_inspection", side_effect=runner):
            item._on_action(_Sample(event(action_id="inspect-1"))); self.assertTrue(started.wait(1))
            item._on_action(_Sample(event("stop", action_id="stop-1"))); item._worker.join(2)
        by_id = {doc["action_id"]: doc for doc in item._results.documents}
        self.assertEqual(by_id["stop-1"]["status"], "success")
        self.assertEqual(by_id["inspect-1"]["status"], "failure")


if __name__ == "__main__":
    unittest.main()
