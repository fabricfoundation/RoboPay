from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


PROFILE = Path(__file__).resolve().parents[1]
MODULE_PATH = PROFILE / "bridge" / "dobot_cra_zenoh_bridge.py"
SPEC = importlib.util.spec_from_file_location("dobot_cra_zenoh_bridge", MODULE_PATH)
assert SPEC and SPEC.loader
bridge_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge_module
SPEC.loader.exec_module(bridge_module)


class FakeExecutor:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    def execute(self, action, skill):
        self.calls += 1
        if self.error:
            raise self.error
        return {
            "message": "fake completed",
            "projectName": skill["project_name"],
            "artifactSha256": skill["project_artifact_sha256"],
        }

    def close(self):
        return None


class FakeController:
    def __init__(self, modes, projects):
        self.modes = iter(modes)
        self.projects = iter(projects)
        self.run_calls = []
        self.stop_calls = 0

    def robot_mode(self):
        return next(self.modes)

    def get_script_name(self):
        return next(self.projects)

    def run_script(self, project):
        self.run_calls.append(project)

    def stop(self):
        self.stop_calls += 1

    def close(self):
        return None


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = json.loads(
            (PROFILE / "bridge" / "config.example.json").read_text(encoding="utf-8")
        )
        self.config["replay_db"] = str(Path(self.temporary.name) / "replay.sqlite3")
        self.now = datetime(2026, 7, 22, 4, 0, tzinfo=timezone.utc)
        self.environment = patch.dict(
            os.environ,
            {"ROBOT_PAYEE_ADDRESS": "0x1111111111111111111111111111111111111111"},
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def envelope(self, **updates):
        issued = self.now - timedelta(seconds=5)
        body = {
            "actionId": "act-test-001",
            "robotId": self.config["robot_id"],
            "skillId": "cra_two_cycle_pick_place",
            "params": {},
            "paramsHash": bridge_module.canonical_params_hash({}),
            "idempotencyKey": "idem-test-001",
            "payment": {
                "provider": "x402",
                "network": "eip155:84532",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "amount": "2000",
                "payTo": os.environ["ROBOT_PAYEE_ADDRESS"],
                "authorizationId": "auth-test-001",
                "verified": True,
                "status": "authorized",
                "settled": False,
                "issuedAt": issued.isoformat().replace("+00:00", "Z"),
                "expiresAt": (issued + timedelta(minutes=5))
                .isoformat()
                .replace("+00:00", "Z"),
            },
        }
        body.update(updates)
        return body

    def test_valid_action_produces_correlated_success(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        result = bridge.process_raw(json.dumps(self.envelope()), now=self.now)
        self.assertEqual("success", result["status"])
        self.assertEqual("act-test-001", result["actionId"])
        self.assertTrue(result["settlementEligible"])
        self.assertEqual(1, executor.calls)
        encoded = json.dumps(result)
        self.assertNotIn("auth-test-001", encoded)
        self.assertNotIn(os.environ["ROBOT_PAYEE_ADDRESS"], encoded)

    def test_duplicate_is_loaded_from_durable_store_without_actuation(self):
        first_executor = FakeExecutor()
        first_bridge = bridge_module.Bridge(self.config, first_executor)
        first_bridge.process_raw(json.dumps(self.envelope()), now=self.now)

        second_executor = FakeExecutor()
        second_bridge = bridge_module.Bridge(self.config, second_executor)
        result = second_bridge.process_raw(json.dumps(self.envelope()), now=self.now)
        self.assertEqual("error", result["status"])
        self.assertEqual("DUPLICATE", result["error"]["code"])
        self.assertTrue(result["delivery"]["cached"])
        self.assertFalse(result["delivery"]["robotActuated"])
        self.assertEqual("DUPLICATE", result["delivery"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(0, second_executor.calls)

    def test_idempotency_conflict_never_actuates(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        bridge.process_raw(json.dumps(self.envelope()), now=self.now)
        conflict = self.envelope(actionId="act-test-002")
        result = bridge.process_raw(json.dumps(conflict), now=self.now)
        self.assertEqual("error", result["status"])
        self.assertEqual("IDEMPOTENCY_CONFLICT", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(1, executor.calls)

    def test_payment_authorization_cannot_be_reused_with_new_keys(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        bridge.process_raw(json.dumps(self.envelope()), now=self.now)
        replay = self.envelope(actionId="act-test-002", idempotencyKey="idem-test-002")
        result = bridge.process_raw(json.dumps(replay), now=self.now)
        self.assertEqual("PAYMENT_AUTHORIZATION_REPLAY", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(1, executor.calls)

    def test_params_hash_mismatch_is_rejected_before_actuation(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        body = self.envelope(paramsHash="0" * 64)
        result = bridge.process_raw(json.dumps(body), now=self.now)
        self.assertEqual("PARAMS_HASH_MISMATCH", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(0, executor.calls)

    def test_duplicate_json_fields_are_rejected(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        body = json.dumps(self.envelope())
        duplicate = body.replace(
            '"actionId": "act-test-001",',
            '"actionId": "act-test-001", "actionId": "act-test-002",',
        )
        result = bridge.process_raw(duplicate, now=self.now)
        self.assertEqual("INVALID_JSON", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(0, executor.calls)

    def test_expired_action_is_rejected_before_actuation(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        issued = self.now - timedelta(minutes=10)
        body = self.envelope()
        body["payment"]["issuedAt"] = issued.isoformat().replace("+00:00", "Z")
        body["payment"]["expiresAt"] = (
            (issued + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        )
        result = bridge.process_raw(json.dumps(body), now=self.now)
        self.assertEqual("PAYMENT_EXPIRED", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(0, executor.calls)

    def test_wrong_robot_is_rejected_before_actuation(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        result = bridge.process_raw(
            json.dumps(self.envelope(robotId="another-robot")), now=self.now
        )
        self.assertEqual("WRONG_ROBOT", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(0, executor.calls)

    def test_pre_settled_payment_is_rejected(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        body = self.envelope()
        body["payment"]["settled"] = True
        body["payment"]["status"] = "settled"
        result = bridge.process_raw(json.dumps(body), now=self.now)
        self.assertEqual("PAYMENT_ALREADY_SETTLED", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(0, executor.calls)

    def test_payment_types_are_strict(self):
        executor = FakeExecutor()
        bridge = bridge_module.Bridge(self.config, executor)
        body = self.envelope()
        body["payment"]["amount"] = 2000
        result = bridge.process_raw(json.dumps(body), now=self.now)
        self.assertEqual("PAYMENT_INVALID", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])
        self.assertEqual(0, executor.calls)

    def test_execution_error_is_structured_and_not_settlement_eligible(self):
        executor = FakeExecutor(
            bridge_module.BridgeError("ACTION_TIMEOUT", "controller timed out")
        )
        bridge = bridge_module.Bridge(self.config, executor)
        result = bridge.process_raw(json.dumps(self.envelope()), now=self.now)
        self.assertEqual("error", result["status"])
        self.assertEqual("ACTION_TIMEOUT", result["error"]["code"])
        self.assertFalse(result["settlementEligible"])

    def test_dry_run_is_pending_and_never_settlement_eligible(self):
        bridge = bridge_module.Bridge(self.config, bridge_module.DryRunExecutor())
        result = bridge.process_raw(json.dumps(self.envelope()), now=self.now)
        self.assertEqual("pending", result["status"])
        self.assertTrue(result["result"]["dryRun"])
        self.assertFalse(result["settlementEligible"])

    def test_dobot_state_machine_completes_only_after_running_then_idle(self):
        executor = object.__new__(bridge_module.DobotExecutor)
        executor.config = self.config
        executor.controller = FakeController(
            modes=[5, 7, 5], projects=[None, "test", None]
        )
        action = bridge_module.parse_action(
            json.dumps(self.envelope()), self.config["robot_id"], now=self.now
        )
        result = executor.execute(action, self.config["skills"][action.skill_id])
        self.assertEqual(["test"], executor.controller.run_calls)
        self.assertEqual(5, result["controllerEvidence"]["completionMode"])
        self.assertEqual(0, executor.controller.stop_calls)

    def test_dobot_failure_attempts_stop_once(self):
        executor = object.__new__(bridge_module.DobotExecutor)
        executor.config = self.config
        executor.controller = FakeController(modes=[5, 9], projects=[None, "test"])
        action = bridge_module.parse_action(
            json.dumps(self.envelope()), self.config["robot_id"], now=self.now
        )
        with self.assertRaises(bridge_module.BridgeError) as raised:
            executor.execute(action, self.config["skills"][action.skill_id])
        self.assertEqual("CONTROLLER_ERROR", raised.exception.code)
        self.assertEqual(1, executor.controller.stop_calls)


if __name__ == "__main__":
    unittest.main()
