"""Correlated terminal results and replay protection for the X2 bridge."""
from dataclasses import dataclass
from threading import Lock
from typing import Any
import json, time

@dataclass(frozen=True)
class ExecutionResult:
    action_id: str
    robot_id: str
    idempotency_key: str
    status: str
    metrics: dict[str, Any]
    error: str | None = None
    timestamp: float = 0.0
    def to_json(self) -> str:
        return json.dumps({
            "actionId": self.action_id,
            "robotId": self.robot_id,
            "idempotencyKey": self.idempotency_key,
            "status": self.status,
            "metrics": self.metrics,
            "error": self.error,
            "timestamp": self.timestamp,
        }, separators=(",", ":"))

class ReplayGuard:
    """Process each idempotency key once for the lifetime of this bridge."""
    def __init__(self): self._seen: set[str] = set(); self._lock = Lock()
    def claim(self, key: str) -> bool:
        if not key: return False
        with self._lock:
            if key in self._seen: return False
            self._seen.add(key); return True
    def discard(self, key: str) -> None:
        with self._lock: self._seen.discard(key)

def result(action_id: str, robot_id: str, key: str, status: str, metrics: dict[str, Any], error: str | None = None) -> ExecutionResult:
    return ExecutionResult(action_id, robot_id, key, status, metrics, error, time.time())
