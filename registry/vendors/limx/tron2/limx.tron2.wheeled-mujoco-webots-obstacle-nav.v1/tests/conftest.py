from __future__ import annotations

import json
from typing import Any

from limx_tron2_sim.bridge import _hash
from limx_tron2_sim.contracts import NAVIGATION_SKILL, ROBOT_ID


def correlated_event(
    *,
    action_id: str = "test-action-001",
    idempotency_key: str = "test-idempotency-001",
    payment_nonce: str = "payment-001",
    action: str = NAVIGATION_SKILL,
    skill_id: str | None = None,
    robot_id: str = ROBOT_ID,
    params: dict[str, Any] | None = None,
    include_payment: bool = True,
) -> bytes:
    params = {} if params is None else params
    transaction: dict[str, Any] = {}
    if include_payment:
        transaction = {
            "payment_payload": {"scheme": "exact", "test_payment_nonce": payment_nonce},
            "payment_requirements": {"network": "eip155:84532", "asset": "USDC"},
        }
    return json.dumps(
        {
            "payload": {"action": action, "params": params},
            "action_id": action_id,
            "robot_id": robot_id,
            "skill_id": skill_id or action,
            "params_hash": _hash(params),
            "idempotency_key": idempotency_key,
            "transaction_details": transaction,
            "timestamp": "2026-08-07T00:00:00Z",
        }
    ).encode("utf-8")
