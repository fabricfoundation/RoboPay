"""Parse Fabric tunnel Action Event payloads."""
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ActionEvent:
    action_id: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""


def parse_action_event(raw: bytes) -> Optional[ActionEvent]:
    """Parse a Fabric Action Event from raw bytes.

    Expected schema (tunnel handlers.go PostAction, post-fail-closed-gate
    rewrite)::

        {
          "actionId": "9f2c...",
          "action": "move_forward",
          "params": {"speed": 0.5},
          "timestamp": "2026-01-01T00:00:00Z"
        }

    Returns None on parse failure OR on a structurally valid but
    incomplete event (missing actionId or action) -- a missing action
    must never default to some other action (e.g. "stop"): the caller
    has no registered skill to run and the event must be rejected, not
    silently reinterpreted.
    """
    try:
        event = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(event, dict):
        return None

    action_id = event.get("actionId")
    action = event.get("action")
    if not action_id or not action:
        return None

    params = event.get("params") or {}
    if not isinstance(params, dict):
        return None

    return ActionEvent(
        action_id=action_id,
        action=action,
        params=params,
        timestamp=event.get("timestamp", ""),
    )
