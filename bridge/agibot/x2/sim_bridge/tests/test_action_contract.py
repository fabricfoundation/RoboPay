"""Tests for envelope parsing, validation, and the rules for refusing one.

These need no simulator: the point is that a bad request is stopped before it
ever reaches one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ..x2.action_contract import (
    REQUIRED_FIELDS,
    ActionEnvelope,
    ActionRejected,
    RejectionCode,
    canonical_params_hash,
)

ROBOT = "x2-sim-001"
PARAMS = {"puck_x": 0.24, "puck_y": 0.16, "goal_x": 0.26, "goal_y": 0.30}


def body(**overrides) -> dict:
    payload = {
        "actionId": "act_test",
        "robotId": ROBOT,
        "skillId": "push_to_target",
        "params": dict(PARAMS),
        "idempotencyKey": "idem-test",
        "paramsHash": canonical_params_hash(PARAMS),
        "payment": {
            "provider": "x402",
            "amount": "10000",
            "asset": "USDC",
            "network": "eip155:84532",
            "verified": True,
            "txHash": "0x" + "ab" * 32,
        },
    }
    payload.update(overrides)
    return payload


# -- parsing --------------------------------------------------------------


def test_parses_a_well_formed_envelope():
    envelope = ActionEnvelope.from_bytes(json.dumps(body()).encode())
    assert envelope.action_id == "act_test"
    assert envelope.skill_id == "push_to_target"
    assert envelope.payment.verified is True


def test_round_trip_preserves_every_required_field():
    """The criteria require these to survive routing."""
    envelope = ActionEnvelope.from_json(body())
    out = envelope.to_json()
    for field in REQUIRED_FIELDS:
        assert field in out, field
    assert out["paramsHash"] == canonical_params_hash(PARAMS)


def test_rejects_non_json():
    with pytest.raises(ActionRejected) as exc:
        ActionEnvelope.from_bytes(b"this is not json")
    assert exc.value.code == RejectionCode.MALFORMED


def test_rejects_json_that_is_not_an_object():
    with pytest.raises(ActionRejected) as exc:
        ActionEnvelope.from_bytes(b"[1, 2, 3]")
    assert exc.value.code == RejectionCode.MALFORMED


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_rejects_missing_required_field(field):
    payload = body()
    payload.pop(field)
    with pytest.raises(ActionRejected) as exc:
        ActionEnvelope.from_json(payload)
    assert exc.value.code in (
        RejectionCode.MISSING_FIELD,
        RejectionCode.PAYMENT_MISSING,
    )
    assert field in str(exc.value) or field == "payment"


# -- params hash ----------------------------------------------------------


def test_params_hash_is_order_independent():
    a = canonical_params_hash({"x": 1, "y": 2})
    b = canonical_params_hash({"y": 2, "x": 1})
    assert a == b


def test_accepts_untampered_params():
    ActionEnvelope.from_json(body()).require_untampered_params()


def test_rejects_params_edited_after_signing():
    """Paying for one motion must not authorise a different one."""
    tampered = dict(PARAMS, goal_x=PARAMS["goal_x"] + 0.01)
    envelope = ActionEnvelope.from_json(body(params=tampered))
    with pytest.raises(ActionRejected) as exc:
        envelope.require_untampered_params()
    assert exc.value.code == RejectionCode.PARAMS_TAMPERED


# -- addressing and expiry ------------------------------------------------


def test_rejects_an_envelope_for_another_robot():
    envelope = ActionEnvelope.from_json(body(robotId="some-other-robot"))
    with pytest.raises(ActionRejected) as exc:
        envelope.require_robot(ROBOT)
    assert exc.value.code == RejectionCode.UNKNOWN_ROBOT


def test_accepts_an_unexpired_envelope():
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    ActionEnvelope.from_json(body(expiresAt=later)).require_unexpired()


def test_rejects_an_expired_envelope():
    earlier = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    envelope = ActionEnvelope.from_json(body(expiresAt=earlier))
    with pytest.raises(ActionRejected) as exc:
        envelope.require_unexpired()
    assert exc.value.code == RejectionCode.EXPIRED


def test_envelope_without_expiry_never_expires():
    ActionEnvelope.from_json(body()).require_unexpired()


def test_rejects_unparseable_expiry():
    with pytest.raises(ActionRejected) as exc:
        ActionEnvelope.from_json(body(expiresAt="soonish"))
    assert exc.value.code == RejectionCode.MALFORMED


# -- payment --------------------------------------------------------------


def test_rejects_unverified_payment():
    payment = dict(body()["payment"], verified=False)
    payment.pop("txHash")
    envelope = ActionEnvelope.from_json(body(payment=payment))
    with pytest.raises(ActionRejected) as exc:
        envelope.require_verified_payment()
    assert exc.value.code == RejectionCode.PAYMENT_MISSING


def test_rejects_verified_payment_without_settlement_reference():
    payment = dict(body()["payment"])
    payment.pop("txHash")
    envelope = ActionEnvelope.from_json(body(payment=payment))
    with pytest.raises(ActionRejected) as exc:
        envelope.require_verified_payment()
    assert exc.value.code == RejectionCode.PAYMENT_INVALID


def test_accepts_verified_payment_with_tx_hash():
    ActionEnvelope.from_json(body()).require_verified_payment()
