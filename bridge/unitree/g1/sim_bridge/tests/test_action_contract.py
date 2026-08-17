"""Tests for envelope parsing, validation, and the rules for refusing one.

These need no simulator: the point is that a bad request is stopped before it
ever reaches one.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from ..g1.action_contract import (
    REQUIRED_FIELDS,
    ActionEnvelope,
    ActionRejected,
    RejectionCode,
    canonical_params_hash,
)

ROBOT = "g1-sim-001"
PARAMS = {"puck_x": 0.34, "puck_y": -0.20, "goal_x": 0.44, "goal_y": -0.04}


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
    tampered = dict(PARAMS, goal_x=PARAMS["goal_x"] + 0.05)
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


# -- the Fabric tunnel's actual wire format -------------------------------
#
# The tunnel does not publish the flat envelope. `POST /action` sits behind its
# x402 middleware and the handler publishes {payload, transaction_details,
# timestamp}, where payload is the client's body verbatim. Shapes below are
# taken from tunnel/internal/handlers/handlers.go and the x402 v2 types in
# github.com/x402-foundation/x402/types/v2.go, not invented here.


def x402_requirements(**over):
    return dict({
        "scheme": "exact",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "amount": "2000",
        "payTo": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "maxTimeoutSeconds": 30,
    }, **over)


def tunnel_message(inner=None, paid=True, requirements=None):
    """What the tunnel actually puts on robot/tunnel/action."""
    inner_body = dict(inner if inner is not None else body())
    inner_body.pop("payment", None)    # the tunnel's client never sends one
    details = {"payment_requirements": requirements or x402_requirements()}
    if paid:
        details["payment_payload"] = {
            "x402Version": 2,
            "scheme": "exact",
            "network": "eip155:84532",
            "payload": {
                "signature": "0x" + "ab" * 65,
                "authorization": {
                    "from": "0x1111111111111111111111111111111111111111",
                    "to": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
                    "value": "2000",
                    "validAfter": "0",
                    "validBefore": "9999999999",
                    "nonce": "0x" + "cd" * 32,
                },
            },
            "accepted": requirements or x402_requirements(),
        }
    return {
        "payload": inner_body,
        "transaction_details": details,
        "timestamp": "2026-08-17T17:30:00Z",
    }


def test_parses_the_tunnels_wrapped_envelope():
    envelope = ActionEnvelope.from_json(tunnel_message())
    assert envelope.skill_id == "push_to_target"
    assert envelope.payment.provider == "x402"
    assert envelope.payment.network == "eip155:84532"
    assert envelope.payment.amount == "2000"


def test_a_tunnel_message_is_accepted_without_a_settlement_reference():
    """x402 settles *after* the handler runs, so no txHash exists on arrival.

    Requiring one rejected every message the real tunnel sends.
    """
    envelope = ActionEnvelope.from_json(tunnel_message())
    assert envelope.payment.tx_hash is None
    assert envelope.payment.authorization_ref
    envelope.require_verified_payment()


def test_a_tunnel_message_without_a_payment_payload_is_refused():
    envelope = ActionEnvelope.from_json(tunnel_message(paid=False))
    assert envelope.payment.verified is False
    with pytest.raises(ActionRejected) as exc:
        envelope.require_verified_payment()
    assert exc.value.code == RejectionCode.PAYMENT_MISSING


def test_the_request_body_cannot_assert_its_own_payment():
    """The body is attacker-controlled; transaction_details is not.

    A caller who reaches the topic must not be able to claim a verified
    payment by putting one in the payload the tunnel forwards verbatim.
    """
    forged = dict(body())
    forged["payment"] = {
        "provider": "x402", "amount": "999999", "asset": "USDC",
        "network": "eip155:84532", "verified": True, "txHash": "0x" + "ff" * 32,
    }
    envelope = ActionEnvelope.from_json(tunnel_message(inner=forged, paid=False))
    assert envelope.payment.verified is False
    assert envelope.payment.tx_hash is None
    assert envelope.payment.amount != "999999"
    with pytest.raises(ActionRejected) as exc:
        envelope.require_verified_payment()
    assert exc.value.code == RejectionCode.PAYMENT_MISSING


def test_params_hash_still_guards_a_tunnel_message():
    tampered = body()
    tampered["params"] = dict(
        tampered["params"], goal_x=tampered["params"]["goal_x"] + 0.02
    )
    envelope = ActionEnvelope.from_json(tunnel_message(inner=tampered))
    with pytest.raises(ActionRejected) as exc:
        envelope.require_untampered_params()
    assert exc.value.code == RejectionCode.PARAMS_TAMPERED


def test_a_flat_envelope_is_still_accepted():
    """Both shapes work, so the test client and the tunnel share one parser."""
    envelope = ActionEnvelope.from_json(body())
    assert envelope.payment.verified is True
    envelope.require_verified_payment()
