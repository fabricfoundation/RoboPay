"""
Unit tests for bridge/action_validator.py -- the payment/security gate.

Each test targets one specific way a malicious or malformed action
envelope could try to reach the simulator without a valid, verified,
unsettled x402 authorization, and asserts it is rejected with the
correct error code.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(THIS_DIR, "..", "bridge"))

from action_validator import (  # noqa: E402
    validate_envelope, canonical_params_hash, ValidationError,
)

NOW = datetime(2026, 7, 31, 10, 2, 0, tzinfo=timezone.utc)


def make_valid_envelope():
    params = {"goal_x": 5.0, "goal_y": 0.0, "max_time_sec": 60}
    return {
        "actionId": "act_test_0001",
        "robotId": "booster-k1-sim-01",
        "skillId": "k1_navigate_avoid_obstacles",
        "params": params,
        "paramsHash": canonical_params_hash(params),
        "idempotencyKey": "idem_test_0001",
        "payment": {
            "provider": "x402",
            "authorizationId": "auth_test_0001",
            "verified": True,
            "status": "authorized",
            "settled": False,
            "network": "eip155:84532",
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "amount": "1000",
            "payTo": "0xRobotPayeeAddress",
            "issuedAt": "2026-07-31T10:00:00Z",
            "expiresAt": "2026-07-31T10:05:00Z",
        },
    }


def test_valid_envelope_passes():
    envelope = make_valid_envelope()
    result = validate_envelope(envelope, now=NOW)
    assert result.action_id == "act_test_0001"
    assert result.robot_id == "booster-k1-sim-01"


def test_missing_field_rejected():
    envelope = make_valid_envelope()
    del envelope["idempotencyKey"]
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "missing_fields"


def test_wrong_skill_id_rejected():
    envelope = make_valid_envelope()
    envelope["skillId"] = "some_other_skill"
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "unknown_skill"


def test_tampered_params_hash_rejected():
    """Params changed after hash was computed -- must be caught."""
    envelope = make_valid_envelope()
    envelope["params"]["goal_x"] = 999.0  # tampered, hash no longer matches
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "params_hash_mismatch"


def test_unverified_payment_rejected():
    envelope = make_valid_envelope()
    envelope["payment"]["verified"] = False
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "payment_not_verified"


def test_pending_payment_rejected():
    """Payment not yet authorized (still pending) must not execute."""
    envelope = make_valid_envelope()
    envelope["payment"]["status"] = "pending"
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "payment_not_authorized"


def test_already_settled_payment_rejected():
    """Core anti-fraud check: a payment claiming settled=true before
    execution must be rejected (rejectAlreadySettled policy) -- this
    prevents double-spend / replaying an already-paid settlement."""
    envelope = make_valid_envelope()
    envelope["payment"]["settled"] = True
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "payment_already_settled"


def test_expired_payment_rejected():
    envelope = make_valid_envelope()
    envelope["payment"]["expiresAt"] = "2026-07-31T10:01:00Z"  # before NOW=10:02
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "payment_expired"


def test_wrong_network_rejected():
    envelope = make_valid_envelope()
    envelope["payment"]["network"] = "eip155:1"  # mainnet, not the configured testnet
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "invalid_network"


def test_wrong_amount_rejected():
    """Paying less than the configured price must not execute."""
    envelope = make_valid_envelope()
    envelope["payment"]["amount"] = "1"
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "invalid_amount"


def test_missing_params_fields_rejected():
    envelope = make_valid_envelope()
    del envelope["params"]["goal_x"]
    envelope["paramsHash"] = canonical_params_hash(envelope["params"])
    with pytest.raises(ValidationError) as exc:
        validate_envelope(envelope, now=NOW)
    assert exc.value.code == "invalid_params"
