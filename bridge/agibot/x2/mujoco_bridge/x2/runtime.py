"""Pure-Python pay-to-sim runtime used by demos and integration tests."""
from typing import Any, Callable
from .result import ReplayGuard, result

class X2Runtime:
    def __init__(self, robot_id: str, execute: Callable[[str, dict[str, Any]], dict[str, Any]], publish: Callable[[dict[str, Any]], None]):
        self.robot_id, self.execute, self.publish, self.replays = robot_id, execute, publish, ReplayGuard()
    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        payload, tx = event.get("payload", {}), event.get("transaction_details", {})
        action_id, key = payload.get("actionId", ""), payload.get("idempotencyKey", "")
        if not action_id or payload.get("robotId") != self.robot_id or not tx.get("payment_payload"):
            out = result(action_id, self.robot_id, key, "FAILED", {}, "missing payment or correlation fields")
        elif not self.replays.claim(key):
            out = result(action_id, self.robot_id, key, "REPLAY_REJECTED", {}, "duplicate idempotency key")
        else:
            try: out = result(action_id, self.robot_id, key, "SUCCESS", self.execute(payload.get("action", ""), payload.get("params", {})))
            except Exception as exc: self.replays.discard(key); out = result(action_id, self.robot_id, key, "FAILED", {}, str(exc))
        data = {"topic": "robot/tunnel/result", **out.__dict__}; self.publish(data); return data
