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


def parse_action_event(raw: bytes) -> Optional[ActionEvent]:
    """Parse a Fabric Action Event from raw bytes.

    Expected schema (tunnel handlers.go:97-104)::

        {
          "payload": {"action": "move_forward", "params": {"speed": 0.5}},
          "transaction_details": {...},
          "timestamp": "2026-01-01T00:00:00Z"
        }

    Returns None on parse failure.
    """
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None

    return ActionEvent(
        action=payload.get("action", "stop"),
        params=payload.get("params") or {},
        timestamp=event.get("timestamp", ""),
        action_id=event.get("action_id", ""),
        robot_id=event.get("robot_id", ""),
        skill_id=event.get("skill_id", ""),
        params_hash=event.get("params_hash", ""),
        idempotency_key=event.get("idempotency_key", ""),
    )
