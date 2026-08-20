"""A forged payment must be rejected by the real facilitator, not by shape alone.

The protocol checks in :mod:`x402` cannot tell a real authorization from a
well-formed forgery — the amount, the asset, the network and the hash shape can
all be perfect on a payload nobody ever signed. Only the facilitator recovers
the signer, so these tests drive the actual x402 facilitator and assert that a
forged authorization is refused before anything is executed or settled.

The network-touching cases are marked ``facilitator`` so they can be deselected
offline; the fail-closed behaviour is checked without a network either way.
"""

from __future__ import annotations

import pytest

from bridge.boston_dynamics.atlas_bridge.facilitator import (
    DEFAULT_FACILITATOR_URL,
    FacilitatorClient,
    FacilitatorVerdict,
    payment_requirements,
)
from bridge.boston_dynamics.atlas_bridge.relay import ActionRelay, ActionRequest
from bridge.boston_dynamics.atlas_bridge.payment import SettlementLedger
from bridge.boston_dynamics.atlas_bridge.task import (
    PAYMENT_NETWORK,
    SKILL_PRICE_RAW,
    USDC_BASE_SEPOLIA,
)
from bridge.boston_dynamics.atlas_bridge.x402 import (
    PaymentPolicy,
    X402Error,
    X402Verifier,
)

PAYEE = "0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8"
PAYER = "0x520C3Ff276456A217c0dFadABeEb2d7081d6cCd4"
RESOURCE = "https://robopay.invalid/atlas/inspect_shelf"


def forged_authorization() -> dict:
    """A payload that is structurally perfect and cryptographically worthless."""
    return {
        "x402Version": 1,
        "scheme": "exact",
        "network": "base-sepolia",
        "payload": {
            "signature": "0x" + "11" * 65,
            "authorization": {
                "from": PAYER,
                "to": PAYEE,
                "value": SKILL_PRICE_RAW,
                "validAfter": "0",
                "validBefore": "9999999999",
                "nonce": "0x" + "22" * 32,
            },
        },
    }


def receipt_with(authorization: dict) -> dict:
    """A receipt that passes every protocol check the bridge applies."""
    return {
        "amount": SKILL_PRICE_RAW,
        "asset": "USDC",
        "network": PAYMENT_NETWORK,
        "txHash": "0x" + "5b" * 32,
        "payer": PAYER,
        "payee": PAYEE,
        "paymentPayload": authorization,
    }


def requirements() -> dict:
    return payment_requirements(pay_to=PAYEE, resource=RESOURCE)


class StubFacilitator:
    def __init__(self, verdict: FacilitatorVerdict) -> None:
        self.verdict = verdict
        self.calls = 0

    def verify(self, payload, requirements):  # noqa: ARG002 - signature parity
        self.calls += 1
        return self.verdict


# -- what the verifier promises about itself -------------------------------
def test_verifier_admits_when_it_is_only_a_protocol_check():
    policy = PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW)
    assert X402Verifier(policy).verifies_authorization is False
    assert X402Verifier(policy, facilitator=FacilitatorClient()).verifies_authorization is True


def test_protocol_checks_alone_accept_a_forgery():
    """The gap this module exists to close, stated as a test."""
    policy = PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW)
    result = X402Verifier(policy).verify(receipt_with(forged_authorization()))
    assert result.valid is True, "shape checks cannot detect a forged signature"


# -- fail-closed behaviour, no network needed ------------------------------
def test_unreachable_facilitator_refuses_the_payment():
    policy = PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW)
    unreachable = FacilitatorClient(url="http://127.0.0.1:1", timeout=1.0)
    result = X402Verifier(policy, facilitator=unreachable, payment_requirements=requirements()).verify(
        receipt_with(forged_authorization())
    )
    assert result.valid is False
    assert result.error is X402Error.FACILITATOR_REJECTED
    assert "unreachable" in result.message


def test_rejected_payment_never_executes_and_never_settles():
    policy = PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW)
    facilitator = StubFacilitator(
        FacilitatorVerdict(False, "invalid_exact_evm_signature", payer=PAYER)
    )
    executions = []

    relay = ActionRelay(
        verifier=X402Verifier(
            policy, facilitator=facilitator, payment_requirements=requirements()
        ),
        ledger=SettlementLedger(),
        skill_executor=lambda request: executions.append(request) or {"success": True},
    )
    result = relay.handle_action(
        ActionRequest(
            action_id="act-forged-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=receipt_with(forged_authorization()),
        )
    )

    assert facilitator.calls == 1
    assert executions == [], "a rejected payment must not reach the simulator"
    assert result.settlement_status == "skipped"
    assert result.http_status in (400, 402)


def test_facilitator_verdict_is_required_to_be_explicitly_true():
    policy = PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW)
    for verdict in (
        FacilitatorVerdict(False, "invalid_exact_evm_signature"),
        FacilitatorVerdict(False, "", reachable=False),
    ):
        result = X402Verifier(
            policy, facilitator=StubFacilitator(verdict), payment_requirements=requirements()
        ).verify(receipt_with(forged_authorization()))
        assert result.valid is False


# -- the real facilitator ---------------------------------------------------
@pytest.mark.facilitator
def test_live_facilitator_rejects_a_forged_authorization():
    """Drives https://x402.org/facilitator and expects a signature rejection."""
    verdict = FacilitatorClient().verify(forged_authorization(), requirements())

    assert verdict.reachable, f"facilitator was unreachable: {verdict.reason}"
    assert verdict.is_valid is False
    assert "signature" in verdict.reason, verdict.reason


@pytest.mark.facilitator
def test_live_facilitator_rejection_blocks_the_whole_action():
    policy = PaymentPolicy(network=PAYMENT_NETWORK, asset="USDC", amount=SKILL_PRICE_RAW)
    executions = []
    relay = ActionRelay(
        verifier=X402Verifier(
            policy,
            facilitator=FacilitatorClient(),
            payment_requirements=requirements(),
        ),
        ledger=SettlementLedger(),
        skill_executor=lambda request: executions.append(request) or {"success": True},
    )
    result = relay.handle_action(
        ActionRequest(
            action_id="act-live-forged-1",
            robot_id="atlas-sim-01",
            skill_id="inspect_shelf",
            params={},
            payment_header=receipt_with(forged_authorization()),
        )
    )
    assert executions == []
    assert result.settlement_status == "skipped"


def test_requirements_are_built_from_the_profile():
    built = requirements()
    assert built["maxAmountRequired"] == SKILL_PRICE_RAW
    assert built["asset"] == USDC_BASE_SEPOLIA
    assert built["payTo"] == PAYEE
    assert DEFAULT_FACILITATOR_URL.startswith("https://")
