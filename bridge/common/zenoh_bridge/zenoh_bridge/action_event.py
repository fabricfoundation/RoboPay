"""Parse Fabric tunnel Action Event payloads."""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ActionEvent:
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    action_id: str = ""
    robot_id: str = ""
    skill_id: str = ""
    params_hash: str = ""
    idempotency_key: str = ""
    payment_payload: Optional[Dict[str, Any]] = None
    payment_requirements: Optional[Dict[str, Any]] = None


#: The tunnel forwards the caller's request body verbatim (handlers.go), so the
#: identity fields arrive in whatever casing the caller used. Accepting both
#: spellings is what stops a working local demo from silently ignoring a real
#: Fabric request.
_FIELD_ALIASES = {
    "action_id": ("action_id", "actionId"),
    "robot_id": ("robot_id", "robotId"),
    "skill_id": ("skill_id", "skillId"),
    "idempotency_key": ("idempotency_key", "idempotencyKey"),
    "params_hash": ("params_hash", "paramsHash"),
    "params": ("params", "parameters"),
}


def _field(payload: Dict[str, Any], name: str, default: Any = "") -> Any:
    for key in _FIELD_ALIASES.get(name, (name,)):
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def parse_action_event(raw: bytes) -> Optional[ActionEvent]:
    """Parse a Fabric Action Event from raw bytes.

    Expected schema (tunnel handlers.go)::

        {
          "payload": {
            "action": "inspect_shelf",
            "params": {"maxDurationSec": 30},
            "action_id": "act-001",          # or "actionId"
            "robot_id": "atlas-sim-01",      # or "robotId"
            "skill_id": "inspect_shelf",     # or "skillId"
            "idempotency_key": "idem-001"    # or "idempotencyKey"
          },
          "transaction_details": {
            "payment_payload": {...},
            "payment_requirements": {...}
          },
          "timestamp": "2026-01-01T00:00:00Z"
        }

    ``params_hash`` is recomputed from the parameters rather than trusted from
    the wire, so a caller cannot claim one set of parameters and send another.
    It is always emitted in the ``sha256:<hex>`` form the execution mapping
    declares, including for an empty parameter set — a bridge that emitted an
    empty string there would not match its own published contract.

    ``skill_id`` is never inferred from ``action``: a caller that omits it has
    not said which registered skill it is paying for, and the bridge refuses the
    envelope rather than guessing.

    Returns None on parse failure.
    """
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(event, dict):
        return None

    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    params = _field(payload, "params", {}) or {}
    if not isinstance(params, dict):
        return None
    # Canonical form, computed the same way for every parameter set including
    # the empty one, so the published sha256:<hex> contract always holds.
    params_bytes = json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")

    details = event.get("transaction_details") or {}
    if not isinstance(details, dict):
        details = {}

    action = payload.get("action", "stop")
    return ActionEvent(
        action=action,
        params=params,
        timestamp=event.get("timestamp", ""),
        action_id=str(_field(payload, "action_id")),
        robot_id=str(_field(payload, "robot_id")),
        skill_id=str(_field(payload, "skill_id")),
        params_hash=f"sha256:{hashlib.sha256(params_bytes).hexdigest()}",
        idempotency_key=str(_field(payload, "idempotency_key")),
        payment_payload=details.get("payment_payload") or details.get("paymentPayload"),
        payment_requirements=(
            details.get("payment_requirements") or details.get("paymentRequirements")
        ),
    )
