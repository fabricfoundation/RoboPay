"""Parse Fabric tunnel Action Event payloads."""
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
    transaction_details: Dict[str, Any] = field(default_factory=dict)


def parse_action_event(raw: bytes) -> Optional[ActionEvent]:
    """Parse a Fabric Action Event from raw bytes.

    Expected schema (tunnel handlers.go:97-104)::

        {
          "payload": {"action": "move_forward", "params": {"speed": 0.5}},
          "transaction_details": {...},
          "timestamp": "2026-01-01T00:00:00Z"
        }

    Returns None on parse failure or when the payload does not name an
    action: there is no default action, so an unnamed request can never
    actuate a robot (fail closed).
    """
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    action = payload.get("action") or payload.get("skill_id") or payload.get("skillId")
    correlation = {
        "action_id": event.get("action_id"),
        "robot_id": event.get("robot_id"),
        "skill_id": event.get("skill_id") or event.get("skillId"),
        "params_hash": event.get("params_hash") or event.get("paramsHash"),
        "idempotency_key": event.get("idempotency_key") or event.get("idempotencyKey"),
    }
    if not isinstance(action, str) or not action.strip():
        return None
    if any(not isinstance(value, str) or not value.strip() for value in correlation.values()):
        # A bridge must never execute an event it cannot report back to the
        # Tunnel as the exact same paid action.
        return None
    if action.strip() != correlation["skill_id"].strip():
        return None

    params = payload.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None
    transaction_details = event.get("transaction_details") or {}
    if not isinstance(transaction_details, dict):
        transaction_details = {}

    return ActionEvent(
        action=action.strip(),
        params=params,
        timestamp=event.get("timestamp", ""),
        action_id=correlation["action_id"].strip(),
        robot_id=correlation["robot_id"].strip(),
        skill_id=correlation["skill_id"].strip(),
        params_hash=correlation["params_hash"].strip(),
        idempotency_key=correlation["idempotency_key"].strip(),
        transaction_details=transaction_details,
    )
