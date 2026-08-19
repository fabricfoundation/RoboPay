"""x402 payment safety tests.

Proves:
1. Unpaid request → HTTP 402 → no execution → no settlement
2. Invalid payment → rejected → no execution → no settlement
3. Valid payment → execution → success → settlement approved
4. Valid payment → execution → failure → NO settlement
5. Replay detection → rejected → no settlement
"""

from __future__ import annotations

import time

import pytest


from bridge.boston_dynamics.atlas_bridge.x402 import (
    X402Verifier,
    X402Error,
    PaymentPolicy,
)
from bridge.boston_dynamics.atlas_bridge.payment import (
    SettlementLedger,
    SettlementStatus,
)
from bridge.boston_dynamics.atlas_bridge.relay import (
    ActionRelay,
    ActionRequest,
)


def _valid_receipt(
    amount: str = "10000",
    asset: str = "USDC",
    network: str = "eip155:84532",
    tx_hash: str = "0x372323a755883be6a4feeda46a9266b9c0c310782018b73fc0639bcb764a557b",
) -> dict:
    return {
        "amount": amount,
        "asset": asset,
        "network": network,
        "txHash": tx_hash,
        "payer": "0xPayer",
        "payee": "0xPayee",
    }


def _make_policy() -> PaymentPolicy:
    return PaymentPolicy(
        network="eip155:84532",
        asset="USDC",
        amount="10000",
        settle_on_failure=False,
        replay_protection=True,
    )


class TestX402Verification:
    def test_missing_payment_returns_402(self):
        verifier = X402Verifier(_make_policy())
        result = verifier.verify(None)
        assert not result.valid
        assert result.error == X402Error.MISSING_PAYMENT

    def test_malformed_json_rejected(self):
        verifier = X402Verifier(_make_policy())
        result = verifier.verify("not-json{{{")
        assert not result.valid
        assert result.error == X402Error.INVALID_FORMAT

    def test_missing_tx_hash_rejected(self):
        verifier = X402Verifier(_make_policy())
        result = verifier.verify({"amount": "10000", "network": "eip155:84532"})
        assert not result.valid
        assert result.error == X402Error.INVALID_FORMAT

    def test_amount_mismatch_rejected(self):
        verifier = X402Verifier(_make_policy())
        result = verifier.verify(_valid_receipt(amount="5000"))
        assert not result.valid
        assert result.error == X402Error.AMOUNT_MISMATCH

    def test_network_mismatch_rejected(self):
        verifier = X402Verifier(_make_policy())
        result = verifier.verify(_valid_receipt(network="eip155:1"))
        assert not result.valid
        assert result.error == X402Error.NETWORK_MISMATCH

    def test_asset_mismatch_rejected(self):
        verifier = X402Verifier(_make_policy())
        result = verifier.verify(_valid_receipt(asset="USDT"))
        assert not result.valid
        assert result.error == X402Error.ASSET_MISMATCH

    def test_valid_receipt_accepted(self):
        verifier = X402Verifier(_make_policy())
        result = verifier.verify(_valid_receipt())
        assert result.valid
        assert result.receipt is not None
        assert result.receipt.tx_hash == "0x372323a755883be6a4feeda46a9266b9c0c310782018b73fc0639bcb764a557b"

    def test_replay_detected(self):
        verifier = X402Verifier(_make_policy())
        first = verifier.verify(_valid_receipt(tx_hash="0x71a31d7f4889b96c8d8e834fad08c6251221ebfd23fd0eadf14ebc89b25110bf"))
        assert first.valid
        second = verifier.verify(_valid_receipt(tx_hash="0x71a31d7f4889b96c8d8e834fad08c6251221ebfd23fd0eadf14ebc89b25110bf"))
        assert not second.valid
        assert second.error == X402Error.REPLAY_DETECTED

    def test_different_tx_hashes_accepted(self):
        verifier = X402Verifier(_make_policy())
        first = verifier.verify(_valid_receipt(tx_hash="0x8d28a34ccbea2aedf5f06c61d746cb3de86dfa97066d132c3e98875950d0816a"))
        assert first.valid
        second = verifier.verify(_valid_receipt(tx_hash="0x8ecfa169fd5234efac72555985bfcb096d2aa631ce0a9780669fa77e4d10272e"))
        assert second.valid

    def test_expired_receipt_rejected(self):
        verifier = X402Verifier(_make_policy())
        receipt = _valid_receipt()
        receipt["expiry"] = time.time() - 100
        result = verifier.verify(receipt)
        assert not result.valid
        assert result.error == X402Error.EXPIRED


class TestSettlementLedger:
    def test_unpaid_records_skipped(self):
        ledger = SettlementLedger()
        entry = ledger.record_unpaid("act-1", "inspect_shelf", "atlas-01")
        assert entry.status == SettlementStatus.SKIPPED_UNPAID

    def test_settle_on_success(self):
        ledger = SettlementLedger()
        ledger.record_execution_start("act-1", "navigate", "atlas", "0xd0ef2db67c3551d36f0ad1420a5853c7464e9a68f6dd5f4aa7ae76eb2f824481", "10000", "USDC", "eip155:84532")
        entry = ledger.settle_on_success("act-1", block_number=12345)
        assert entry is not None
        assert entry.status == SettlementStatus.SETTLED
        assert entry.block_number == 12345

    def test_skip_on_failure(self):
        ledger = SettlementLedger()
        ledger.record_execution_start("act-1", "navigate", "atlas", "0xd0ef2db67c3551d36f0ad1420a5853c7464e9a68f6dd5f4aa7ae76eb2f824481", "10000", "USDC", "eip155:84532")
        entry = ledger.skip_on_failure("act-1", reason="Robot fell")
        assert entry is not None
        assert entry.status == SettlementStatus.SKIPPED_FAILURE
        assert entry.execution_success is False

    def test_no_double_settle(self):
        ledger = SettlementLedger()
        ledger.record_execution_start("act-1", "navigate", "atlas", "0xd0ef2db67c3551d36f0ad1420a5853c7464e9a68f6dd5f4aa7ae76eb2f824481", "10000", "USDC", "eip155:84532")
        first = ledger.settle_on_success("act-1")
        assert first.status == SettlementStatus.SETTLED
        second = ledger.settle_on_success("act-1")
        assert second is not None
        assert second.status == SettlementStatus.SETTLED
        assert ledger.to_dict()["settled"] == 1

    def test_settle_on_success_only(self):
        ledger = SettlementLedger()
        ledger.record_execution_start("act-1", "navigate", "atlas", "0xd0ef2db67c3551d36f0ad1420a5853c7464e9a68f6dd5f4aa7ae76eb2f824481", "10000", "USDC", "eip155:84532")
        ledger.skip_on_failure("act-1", reason="execution failed")
        entry = ledger.get_entry("act-1")
        assert entry.status == SettlementStatus.SKIPPED_FAILURE
        assert entry.execution_success is False

    def test_ledger_to_dict(self):
        ledger = SettlementLedger()
        ledger.record_unpaid("act-1", "nav", "atlas")
        ledger.record_execution_start("act-2", "nav", "atlas", "0xh", "10000", "USDC", "eip155:84532")
        ledger.settle_on_success("act-2")
        d = ledger.to_dict()
        assert d["total"] == 2
        assert d["settled"] == 1
        assert d["skipped_unpaid"] == 1


class TestRelayPaymentGating:
    def _make_relay(self, executor_result: dict | None = None):
        if executor_result is None:
            executor_result = {"success": True, "forward_progress_m": 4.129}
        ledger = SettlementLedger()
        verifier = X402Verifier(_make_policy())
        relay = ActionRelay(
            verifier=verifier,
            ledger=ledger,
            skill_executor=lambda req: executor_result,
        )
        return relay, ledger

    def test_unpaid_request_returns_402(self):
        relay, ledger = self._make_relay()
        request = ActionRequest(
            action_id="act-unpaid-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={"maxDurationSec": 48},
            payment_header=None,
        )
        result = relay.handle_action(request)
        assert result.http_status == 402
        assert result.status == "error"
        assert result.settlement_status == "skipped"
        entries = ledger.get_all()
        assert len(entries) == 1
        assert entries[0].status == SettlementStatus.SKIPPED_UNPAID

    def test_invalid_payment_returns_400(self):
        relay, ledger = self._make_relay()
        request = ActionRequest(
            action_id="act-invalid-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header={"amount": "5000", "network": "eip155:84532", "txHash": "0x7b32195338c9901877c850d2f90e1687f6ee58e516f75840100feece525a4b4d"},
        )
        result = relay.handle_action(request)
        assert result.http_status == 400
        assert result.settlement_status == "skipped"

    def test_valid_payment_success_settles(self):
        relay, ledger = self._make_relay({"success": True, "distance": 4.129})
        request = ActionRequest(
            action_id="act-success-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=_valid_receipt(tx_hash="0xab2510103273047af570a0a59f64490a34d89eeac430a6edc132f0048e2ca28b"),
        )
        result = relay.handle_action(request)
        assert result.http_status == 200
        assert result.status == "success"
        assert result.settlement_status == "settled"
        entry = ledger.get_entry("act-success-1")
        assert entry.status == SettlementStatus.SETTLED

    def test_valid_payment_failure_no_settlement(self):
        relay, ledger = self._make_relay({"success": False, "error_code": "FALL_DETECTED"})
        request = ActionRequest(
            action_id="act-fail-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=_valid_receipt(tx_hash="0xc43a2190c3babd18ac33f957d43deee3a97ab4a8bcc32fa37ceef1a52e2de61a"),
        )
        result = relay.handle_action(request)
        assert result.http_status == 200
        assert result.status == "error"
        assert result.settlement_status == "skipped_failure"
        entry = ledger.get_entry("act-fail-1")
        assert entry.status == SettlementStatus.SKIPPED_FAILURE
        assert entry.execution_success is False

    def test_execution_exception_no_settlement(self):
        def failing_executor(req):
            raise RuntimeError("Simulator crashed")

        ledger = SettlementLedger()
        relay = ActionRelay(
            verifier=X402Verifier(_make_policy()),
            ledger=ledger,
            skill_executor=failing_executor,
        )
        request = ActionRequest(
            action_id="act-crash-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=_valid_receipt(tx_hash="0x827c153165a3b65b94c88de9a25a8a38a51dc2864c2141d470b925bfe5234255"),
        )
        result = relay.handle_action(request)
        assert result.http_status == 500
        assert result.settlement_status == "skipped_failure"

    def test_robot_id_mismatch_rejected(self):
        relay, ledger = self._make_relay()
        request = ActionRequest(
            action_id="act-mismatch-1",
            robot_id="wrong-robot",
            skill_id="inspect_shelf",
            params={},
            payment_header=_valid_receipt(tx_hash="0xc25088046929a7308634ac3c50bdb866464a8f01d3c1180f2f4f9a0f5c7168cf"),
        )
        result = relay.handle_action(request)
        assert result.http_status == 400

    def test_replay_rejected(self):
        relay, ledger = self._make_relay()
        request1 = ActionRequest(
            action_id="act-replay-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=_valid_receipt(tx_hash="0x8b644a37e49f8a08f2079dc3a2343b9d8d8adf86825b2c4f18d81660a3f00581"),
        )
        relay.handle_action(request1)
        request2 = ActionRequest(
            action_id="act-replay-2",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=_valid_receipt(tx_hash="0x8b644a37e49f8a08f2079dc3a2343b9d8d8adf86825b2c4f18d81660a3f00581"),
        )
        result = relay.handle_action(request2)
        assert result.http_status == 409
        assert result.settlement_status == "skipped"

    def test_relay_full_flow_unpaid_then_paid(self):
        relay, ledger = self._make_relay({"success": True})
        req_unpaid = ActionRequest(
            action_id="act-flow-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=None,
        )
        r1 = relay.handle_action(req_unpaid)
        assert r1.http_status == 402

        req_paid = ActionRequest(
            action_id="act-flow-2",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=_valid_receipt(tx_hash="0x57fd4f06b62312cd7f06d54a585dc1173548cc4804a4a9e84b15fcbfcd9ff54a"),
        )
        r2 = relay.handle_action(req_paid)
        assert r2.http_status == 200
        assert r2.settlement_status == "settled"


class TestPaymentContractConsistency:
    """The price and the asset must be one decision, not four copies."""

    def _profile_dir(self):
        from pathlib import Path

        from bridge.boston_dynamics.atlas_bridge import bridge as bridge_module

        return (
            Path(bridge_module.__file__).resolve().parents[3]
            / "registry" / "vendors" / "boston-dynamics" / "atlas"
            / "boston-dynamics.atlas.mujoco-pybullet-webots-shelf-inspection.v1"
        )

    def test_registry_price_matches_the_bridge_price(self):
        import yaml

        from bridge.boston_dynamics.atlas_bridge.task import SKILL_PRICE_USDC

        policy = yaml.safe_load((self._profile_dir() / "payment-policy.yaml").read_text(encoding="utf-8"))
        skills = yaml.safe_load((self._profile_dir() / "skills.yaml").read_text(encoding="utf-8"))
        for entry in policy["policies"]:
            assert entry["priceUSDC"] == SKILL_PRICE_USDC
        for entry in skills["skills"]:
            assert entry["priceUSDC"] == SKILL_PRICE_USDC

    def test_raw_price_is_the_decimal_price(self):
        from decimal import Decimal

        from bridge.boston_dynamics.atlas_bridge.task import (
            SKILL_PRICE_RAW,
            SKILL_PRICE_USDC,
            USDC_DECIMALS,
        )

        assert Decimal(SKILL_PRICE_RAW) == Decimal(SKILL_PRICE_USDC) * (10**USDC_DECIMALS)

    def test_registry_asset_matches_the_bridge_asset(self):
        import yaml

        from bridge.boston_dynamics.atlas_bridge.task import (
            PAYMENT_NETWORK,
            USDC_BASE_SEPOLIA,
        )

        policy = yaml.safe_load((self._profile_dir() / "payment-policy.yaml").read_text(encoding="utf-8"))
        assert policy["asset"]["address"] == USDC_BASE_SEPOLIA
        assert policy["network"] == PAYMENT_NETWORK

    def test_settlement_layers_share_one_asset_address(self):
        from bridge.boston_dynamics.atlas_bridge.settlement import USDC_BASE_SEPOLIA as ledger
        from bridge.boston_dynamics.atlas_bridge.settlement_evidence import USDC_ADDRESS as evidence
        from bridge.boston_dynamics.atlas_bridge.task import USDC_BASE_SEPOLIA as declared

        assert ledger == evidence == declared


class TestTransactionHashValidation:
    """A settlement reference must look like a settlement reference."""

    def _verifier(self):
        from bridge.boston_dynamics.atlas_bridge.task import (
            PAYMENT_NETWORK,
            SKILL_PRICE_RAW,
        )
        from bridge.boston_dynamics.atlas_bridge.x402 import PaymentPolicy, X402Verifier

        return X402Verifier(
            PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW)
        )

    def _receipt(self, tx_hash: str) -> dict:
        from bridge.boston_dynamics.atlas_bridge.task import (
            PAYMENT_NETWORK,
            SKILL_PRICE_RAW,
        )

        return {
            "amount": SKILL_PRICE_RAW,
            "asset": "USDC",
            "network": PAYMENT_NETWORK,
            "txHash": tx_hash,
        }

    @pytest.mark.parametrize(
        "tx_hash",
        [
            "not-a-hash",
            "0xdemo_real_tx_abc123",
            "0xabc123",
            "0x" + "f" * 63,
            "0x" + "f" * 65,
            "f" * 64,
            "",
        ],
    )
    def test_malformed_transaction_hash_is_rejected(self, tx_hash):
        from bridge.boston_dynamics.atlas_bridge.x402 import X402Error

        result = self._verifier().verify(self._receipt(tx_hash))
        assert result.valid is False
        assert result.error in {X402Error.MALFORMED_TX_HASH, X402Error.INVALID_FORMAT}

    def test_well_formed_transaction_hash_is_accepted(self):
        result = self._verifier().verify(self._receipt("0x" + "a1" * 32))
        assert result.valid is True
