from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROFILE_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = PROFILE_ROOT / "bridge" / "agibot_x2_robopay_bridge.py"
SPEC = importlib.util.spec_from_file_location("agibot_x2_robopay_bridge", BRIDGE_PATH)
assert SPEC and SPEC.loader
bridge_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge_module
SPEC.loader.exec_module(bridge_module)


PAYEE = "0x1111111111111111111111111111111111111111"
ASSET = bridge_module.DEFAULT_ASSET
APPROVED_EVIDENCE_SHA256 = {
    "docs/evidence/agibot-x2-historical-physical-evidence-redacted.mp4": (
        "242620d1982bbd1a80778319f6433f49e9ca434e39b83d96ff0268d6856fb70f"
    ),
    "docs/evidence/terminal/agibot-x2-historical-bridge-task8-redacted.png": (
        "46adddfad2d19e3799645a95bd969fd0d458fc03f1c6e106a222639d6b2613e1"
    ),
    "docs/evidence/terminal/agibot-x2-historical-payment-terminal-redacted.png": (
        "0322cb26d6882b911f481a0c48ab123eac9d1d2cf3e9c666d207be4e1f7f3557"
    ),
}


class FakeExecutor:
    def __init__(self, outcome=None, failure=None):
        self.calls = []
        self.outcome = outcome or bridge_module.ExecutionOutcome(
            status="success",
            message="completed",
            task_id=8,
            vendor_state="SUCCESS",
            vendor_code=0,
        )
        self.failure = failure

    def execute(self, action):
        self.calls.append(action)
        if self.failure:
            raise self.failure
        return self.outcome


def valid_envelope():
    params = {"interrupt": True}
    return {
        "actionId": "act_test_x2_wave_001",
        "robotId": "agibot-x2-demo-001",
        "skillId": "x2_right_wave",
        "params": params,
        "paramsHash": bridge_module.canonical_params_hash(params),
        "idempotencyKey": "test-x2-wave-001",
        "payment": {
            "provider": "x402",
            "authorizationId": "auth_test_x2_wave_001",
            "verified": True,
            "status": "authorized",
            "settled": False,
            "network": bridge_module.DEFAULT_NETWORK,
            "asset": ASSET,
            "amount": bridge_module.DEFAULT_AMOUNT,
            "payTo": PAYEE,
            "issuedAt": "2026-07-22T00:00:00Z",
            "expiresAt": "2026-07-22T00:05:00Z",
        },
    }


class BridgeContractTests(unittest.TestCase):
    def setUp(self):
        self.config = bridge_module.ValidationConfig(
            robot_id="agibot-x2-demo-001",
            payee_address=PAYEE,
        )

    def make_bridge(self, executor=None, store=None):
        executor = executor or FakeExecutor()
        store = store or bridge_module.ReplayStore(":memory:")
        instance = bridge_module.Bridge(
            executor,
            store,
            self.config,
            now=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
        )
        return instance, executor, store

    def process(self, instance, envelope):
        return instance.process_raw(json.dumps(envelope, separators=(",", ":")))

    def assert_not_executed(self, mutate, expected_code):
        instance, executor, store = self.make_bridge()
        envelope = valid_envelope()
        mutate(envelope)
        try:
            result = self.process(instance, envelope)
            self.assertEqual("error", result["status"])
            self.assertEqual(expected_code, result["error"]["code"])
            self.assertFalse(result["settlementEligible"])
            self.assertEqual([], executor.calls)
        finally:
            store.close()

    def test_canonical_params_hash_matches_committed_example(self):
        self.assertEqual(
            "945b8598389f04a2fef4e52f80313c4d23abbf6271f40ff7a5fb8d8b2e88abf4",
            bridge_module.canonical_params_hash({"interrupt": True}),
        )

    def test_committed_example_matches_the_action_contract(self):
        raw = (PROFILE_ROOT / "examples" / "action-envelope.right-wave.json").read_text(
            encoding="utf-8"
        )
        example_config = bridge_module.ValidationConfig(
            robot_id="agibot-x2-demo-001",
            payee_address="0x0000000000000000000000000000000000000001",
        )
        action = bridge_module.parse_action(
            raw,
            example_config,
            now=datetime(2026, 7, 22, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual("act_example_x2_wave_001", action.action_id)
        self.assertEqual("auth_example_x2_wave_001", action.payment.authorization_id)

    def test_aimdk_state_value_accepts_direct_integer(self):
        self.assertEqual(3, bridge_module._state_value(3))

    def test_aimdk_state_value_accepts_ros_wrapper(self):
        class RosState:
            value = 3

        self.assertEqual(3, bridge_module._state_value(RosState()))

    def test_valid_explicit_success_is_correlated_and_settlement_eligible(self):
        instance, executor, store = self.make_bridge()
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("success", result["status"])
            self.assertTrue(result["settlementEligible"])
            self.assertEqual("act_test_x2_wave_001", result["actionId"])
            self.assertEqual("x2_right_wave", result["skillId"])
            self.assertEqual(8, result["result"]["vendor"]["taskId"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    def test_running_remains_pending_and_cannot_settle(self):
        outcome = bridge_module.ExecutionOutcome(
            status="pending",
            message="accepted but still running",
            task_id=8,
            vendor_state="RUNNING",
            vendor_code=0,
        )
        instance, executor, store = self.make_bridge(FakeExecutor(outcome))
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("pending", result["status"])
            self.assertFalse(result["settlementEligible"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    def test_dry_run_is_never_settlement_eligible(self):
        instance, _, store = self.make_bridge(bridge_module.DryRunExecutor())
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("pending", result["status"])
            self.assertFalse(result["settlementEligible"])
            self.assertTrue(result["result"]["simulated"])
        finally:
            store.close()

    def test_duplicate_survives_bridge_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "replay.sqlite3"
            first_store = bridge_module.ReplayStore(db_path)
            first_bridge, first_executor, _ = self.make_bridge(store=first_store)
            first = self.process(first_bridge, valid_envelope())
            first_store.close()

            second_store = bridge_module.ReplayStore(db_path)
            second_executor = FakeExecutor()
            second_bridge, _, _ = self.make_bridge(second_executor, second_store)
            try:
                second = self.process(second_bridge, valid_envelope())
                self.assertEqual("success", first["status"])
                self.assertEqual(1, len(first_executor.calls))
                self.assertEqual("DUPLICATE", second["error"]["code"])
                self.assertEqual([], second_executor.calls)
            finally:
                second_store.close()

    def test_duplicate_action_id_with_new_key_is_rejected(self):
        instance, executor, store = self.make_bridge()
        try:
            first = valid_envelope()
            second = copy.deepcopy(first)
            second["idempotencyKey"] = "test-x2-wave-002"
            self.assertEqual("success", self.process(instance, first)["status"])
            result = self.process(instance, second)
            self.assertEqual("DUPLICATE", result["error"]["code"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    def test_reused_payment_authorization_is_rejected(self):
        instance, executor, store = self.make_bridge()
        try:
            first = valid_envelope()
            second = copy.deepcopy(first)
            second["actionId"] = "act_test_x2_wave_002"
            second["idempotencyKey"] = "test-x2-wave-002"
            self.assertEqual("success", self.process(instance, first)["status"])
            result = self.process(instance, second)
            self.assertEqual("DUPLICATE", result["error"]["code"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    def test_wrong_robot_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda envelope: envelope.__setitem__("robotId", "another-robot-001"),
            "WRONG_ROBOT",
        )

    def test_unknown_skill_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda envelope: envelope.__setitem__("skillId", "x2_unvalidated_motion"),
            "UNKNOWN_SKILL",
        )

    def test_invalid_parameter_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda envelope: envelope.__setitem__("params", {"interrupt": "yes"}),
            "INVALID_PARAMS",
        )

    def test_extra_parameter_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda envelope: envelope.__setitem__(
                "params", {"interrupt": True, "motion": 1003}
            ),
            "INVALID_PARAMS",
        )

    def test_tampered_hash_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda envelope: envelope.__setitem__("paramsHash", "0" * 64),
            "PARAMS_HASH_MISMATCH",
        )

    def test_unverified_payment_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda envelope: envelope["payment"].__setitem__("verified", False),
            "PAYMENT_INVALID",
        )

    def test_expired_payment_is_rejected_before_actuation(self):
        def expire(envelope):
            envelope["payment"]["issuedAt"] = "2026-07-21T23:55:00Z"
            envelope["payment"]["expiresAt"] = "2026-07-22T00:00:00Z"

        self.assert_not_executed(
            expire,
            "PAYMENT_EXPIRED",
        )

    def test_long_authorization_ttl_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda envelope: envelope["payment"].__setitem__(
                "expiresAt", "2026-07-22T00:05:01Z"
            ),
            "PAYMENT_INVALID",
        )

    def test_authorization_beyond_future_clock_skew_is_rejected(self):
        def move_beyond_skew(envelope):
            envelope["payment"]["issuedAt"] = "2026-07-22T00:00:31Z"
            envelope["payment"]["expiresAt"] = "2026-07-22T00:05:31Z"

        self.assert_not_executed(move_beyond_skew, "PAYMENT_INVALID")

    def test_authorization_within_future_clock_skew_is_allowed(self):
        instance, executor, store = self.make_bridge()
        envelope = valid_envelope()
        envelope["payment"]["issuedAt"] = "2026-07-22T00:00:30Z"
        envelope["payment"]["expiresAt"] = "2026-07-22T00:05:30Z"
        try:
            result = self.process(instance, envelope)
            self.assertEqual("success", result["status"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    def test_unsafe_time_validation_configuration_is_rejected(self):
        invalid_options = (
            {"max_authorization_ttl_sec": 0},
            {"max_authorization_ttl_sec": 3601},
            {"future_clock_skew_sec": -1},
            {"future_clock_skew_sec": 301},
        )
        for options in invalid_options:
            with self.subTest(options=options), self.assertRaises(ValueError):
                bridge_module.ValidationConfig(
                    robot_id="agibot-x2-demo-001",
                    payee_address=PAYEE,
                    **options,
                )

    def test_mismatched_payment_bindings_are_rejected(self):
        mutations = {
            "network": "eip155:1",
            "asset": "0x2222222222222222222222222222222222222222",
            "amount": "1",
            "payTo": "0x2222222222222222222222222222222222222222",
            "status": "settled",
            "settled": True,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                self.assert_not_executed(
                    lambda envelope, f=field, v=value: envelope["payment"].__setitem__(
                        f, v
                    ),
                    "PAYMENT_INVALID",
                )

    def test_duplicate_json_key_is_rejected(self):
        instance, executor, store = self.make_bridge()
        try:
            raw = '{"actionId":"act_first_0001","actionId":"act_second_0002"}'
            result = instance.process_raw(raw)
            self.assertEqual("INVALID_ENVELOPE", result["error"]["code"])
            self.assertEqual([], executor.calls)
        finally:
            store.close()

    def test_robot_failure_is_error_and_cannot_settle(self):
        outcome = bridge_module.ExecutionOutcome(
            status="error",
            message="vendor rejected",
            error_code="ACTION_REJECTED",
        )
        instance, executor, store = self.make_bridge(FakeExecutor(outcome))
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("error", result["status"])
            self.assertEqual("ACTION_REJECTED", result["error"]["code"])
            self.assertFalse(result["settlementEligible"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    def test_audit_output_does_not_log_wallet_or_authorization(self):
        instance, _, store = self.make_bridge()
        capture = io.StringIO()
        try:
            with contextlib.redirect_stdout(capture):
                self.process(instance, valid_envelope())
            logged = capture.getvalue()
            self.assertNotIn(PAYEE, logged)
            self.assertNotIn("auth_test_x2_wave_001", logged)
            self.assertIn("act_test_x2_wave_001", logged)
        finally:
            store.close()

    def test_only_approved_public_evidence_binaries_are_packaged(self):
        binary_suffixes = {".mp4", ".mov", ".png", ".jpg", ".jpeg", ".zip"}
        packaged = {}
        for path in PROFILE_ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in binary_suffixes:
                continue
            relative_path = path.relative_to(PROFILE_ROOT).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            packaged[relative_path] = digest
            with self.subTest(path=relative_path):
                self.assertIn(relative_path, APPROVED_EVIDENCE_SHA256)
                self.assertEqual(APPROVED_EVIDENCE_SHA256.get(relative_path), digest)
        self.assertEqual(APPROVED_EVIDENCE_SHA256, packaged)


if __name__ == "__main__":
    unittest.main()
