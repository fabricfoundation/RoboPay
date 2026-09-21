from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROFILE_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_PATH = PROFILE_ROOT / "bridge" / "agibot_x2_tier1_bridge.py"
SPEC = importlib.util.spec_from_file_location("agibot_x2_tier1_bridge", BRIDGE_PATH)
assert SPEC and SPEC.loader
bridge_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge_module
SPEC.loader.exec_module(bridge_module)


PAYEE = "0x1111111111111111111111111111111111111111"
ASSET = bridge_module.DEFAULT_ASSET


class FakeExecutor:
    """Stands in for DualSimulatorExecutor without launching real simulators."""

    def __init__(self, mujoco_outcome=None, webots_outcome=None, raise_exc=None):
        self.calls = []
        self.mujoco_outcome = mujoco_outcome or bridge_module.SimOutcome(
            reached_target=True, collided=False, timed_out=False,
            simulator="mujoco", detail="reached target in 7.85s",
        )
        self.webots_outcome = webots_outcome or bridge_module.SimOutcome(
            reached_target=True, collided=False, timed_out=False,
            simulator="webots", detail="reached target in 12.92s",
        )
        self.raise_exc = raise_exc

    def execute(self, action):
        self.calls.append(action)
        if self.raise_exc:
            raise self.raise_exc
        return bridge_module.DualSimOutcome(mujoco=self.mujoco_outcome, webots=self.webots_outcome)

    def close(self):
        return None


def valid_envelope():
    params = {"targetX": 3.0, "targetY": 0.0, "maxDurationSec": 30.0}
    return {
        "actionId": "act_test_x2_nav_001",
        "robotId": "agibot-x2-tier1-demo-001",
        "skillId": "x2_obstacle_avoid_nav",
        "params": params,
        "paramsHash": bridge_module.canonical_params_hash(params),
        "idempotencyKey": "test-x2-nav-001",
        "payment": {
            "provider": "x402",
            "authorizationId": "auth_test_x2_nav_001",
            "verified": True,
            "status": "authorized",
            "settled": False,
            "network": bridge_module.DEFAULT_NETWORK,
            "asset": ASSET,
            "amount": bridge_module.DEFAULT_AMOUNT,
            "payTo": PAYEE,
            "issuedAt": "2026-07-31T00:00:00Z",
            "expiresAt": "2026-07-31T00:05:00Z",
        },
    }


class BridgeContractTests(unittest.TestCase):
    def setUp(self):
        self.config = bridge_module.ValidationConfig(robot_id="agibot-x2-tier1-demo-001", payee_address=PAYEE)

    def make_bridge(self, executor=None, store=None):
        executor = executor or FakeExecutor()
        store = store or bridge_module.ReplayStore(":memory:")
        instance = bridge_module.Bridge(
            executor, store, self.config,
            now=lambda: datetime(2026, 7, 31, tzinfo=timezone.utc),
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

    # --- canonical hashing ---

    def test_canonical_params_hash_matches_committed_example(self):
        params = {"targetX": 3.0, "targetY": 0.0, "maxDurationSec": 30.0}
        self.assertEqual(64, len(bridge_module.canonical_params_hash(params)))
        self.assertEqual(
            bridge_module.canonical_params_hash(params),
            bridge_module.canonical_params_hash(dict(params)),
        )

    def test_committed_example_matches_the_action_contract(self):
        raw = (PROFILE_ROOT / "examples" / "action-envelope.obstacle-avoid-nav.json").read_text(encoding="utf-8")
        envelope = json.loads(raw)
        envelope["payment"]["issuedAt"] = "2026-07-31T00:00:00Z"
        envelope["payment"]["expiresAt"] = "2026-07-31T00:05:00Z"
        envelope["payment"]["payTo"] = PAYEE
        example_config = bridge_module.ValidationConfig(robot_id=envelope["robotId"], payee_address=PAYEE)
        action = bridge_module.parse_action(
            json.dumps(envelope), example_config,
            now=datetime(2026, 7, 31, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual("x2_obstacle_avoid_nav", action.skill_id)

    # --- happy path ---

    def test_both_simulators_success_is_settlement_eligible(self):
        instance, executor, store = self.make_bridge()
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("success", result["status"])
            self.assertTrue(result["settlementEligible"])
            self.assertTrue(result["result"]["simToSimAgreement"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    # --- Sim-to-Sim disagreement ---

    def test_mujoco_success_webots_collision_is_not_settlement_eligible(self):
        executor = FakeExecutor(
            webots_outcome=bridge_module.SimOutcome(
                reached_target=False, collided=True, timed_out=False,
                simulator="webots", detail="collided with obstacle",
            )
        )
        instance, _, store = self.make_bridge(executor)
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("error", result["status"])
            self.assertFalse(result["settlementEligible"])
        finally:
            store.close()

    def test_mujoco_collision_webots_success_is_not_settlement_eligible(self):
        executor = FakeExecutor(
            mujoco_outcome=bridge_module.SimOutcome(
                reached_target=False, collided=True, timed_out=False,
                simulator="mujoco", detail="collided with obstacle",
            )
        )
        instance, _, store = self.make_bridge(executor)
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("error", result["status"])
            self.assertFalse(result["settlementEligible"])
        finally:
            store.close()

    def test_both_timed_out_is_not_settlement_eligible(self):
        timeout_outcome = bridge_module.SimOutcome(
            reached_target=False, collided=False, timed_out=True,
            simulator="x", detail="max_duration_sec elapsed",
        )
        executor = FakeExecutor(mujoco_outcome=timeout_outcome, webots_outcome=timeout_outcome)
        instance, _, store = self.make_bridge(executor)
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("error", result["status"])
            self.assertFalse(result["settlementEligible"])
        finally:
            store.close()

    def test_simulator_exception_is_error_and_cannot_settle(self):
        executor = FakeExecutor(raise_exc=RuntimeError("simulator crashed"))
        instance, _, store = self.make_bridge(executor)
        try:
            result = self.process(instance, valid_envelope())
            self.assertEqual("error", result["status"])
            self.assertEqual("EXECUTION_FAILED", result["error"]["code"])
            self.assertFalse(result["settlementEligible"])
        finally:
            store.close()

    # --- replay / duplicate protection ---

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

    def test_reused_payment_authorization_with_new_action_is_rejected(self):
        instance, executor, store = self.make_bridge()
        try:
            first = valid_envelope()
            second = copy.deepcopy(first)
            second["actionId"] = "act_test_x2_nav_002"
            second["idempotencyKey"] = "test-x2-nav-002"
            self.assertEqual("success", self.process(instance, first)["status"])
            result = self.process(instance, second)
            self.assertEqual("DUPLICATE", result["error"]["code"])
            self.assertEqual(1, len(executor.calls))
        finally:
            store.close()

    # --- reject-before-actuation (rejected BEFORE the simulator ever runs) ---

    def test_wrong_robot_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e.__setitem__("robotId", "another-robot-001"), "WRONG_ROBOT"
        )

    def test_unknown_skill_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e.__setitem__("skillId", "x2_unvalidated_skill"), "UNKNOWN_SKILL"
        )

    def test_invalid_param_type_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e.__setitem__("params", {"targetX": "far", "targetY": 0.0, "maxDurationSec": 30.0}),
            "INVALID_PARAMS",
        )

    def test_negative_max_duration_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e["params"].__setitem__("maxDurationSec", -5.0), "INVALID_PARAMS"
        )

    def test_extra_param_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e["params"].__setitem__("extraField", 1), "INVALID_PARAMS"
        )

    def test_tampered_hash_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e.__setitem__("paramsHash", "0" * 64), "PARAMS_HASH_MISMATCH"
        )

    def test_unverified_payment_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e["payment"].__setitem__("verified", False), "PAYMENT_INVALID"
        )

    def test_settled_payment_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e["payment"].__setitem__("settled", True), "PAYMENT_INVALID"
        )

    def test_expired_payment_is_rejected_before_actuation(self):
        def expire(e):
            e["payment"]["issuedAt"] = "2026-07-30T23:55:00Z"
            e["payment"]["expiresAt"] = "2026-07-31T00:00:00Z"  # == now, so already expired

        self.assert_not_executed(expire, "PAYMENT_EXPIRED")

    def test_long_authorization_ttl_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e["payment"].__setitem__("expiresAt", "2026-07-31T00:05:01Z"),
            "PAYMENT_INVALID",
        )

    def test_mismatched_payee_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e["payment"].__setitem__("payTo", "0x2222222222222222222222222222222222222222"),
            "PAYMENT_INVALID",
        )

    def test_wrong_amount_is_rejected_before_actuation(self):
        self.assert_not_executed(
            lambda e: e["payment"].__setitem__("amount", "1"), "PAYMENT_INVALID"
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

    def test_audit_output_does_not_log_payee_or_authorization(self):
        import contextlib
        import io

        instance, _, store = self.make_bridge()
        capture = io.StringIO()
        try:
            with contextlib.redirect_stdout(capture):
                self.process(instance, valid_envelope())
            logged = capture.getvalue()
            self.assertNotIn(PAYEE, logged)
            self.assertNotIn("auth_test_x2_nav_001", logged)
            self.assertIn("act_test_x2_nav_001", logged)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
