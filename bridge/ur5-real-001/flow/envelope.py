"""Unified task envelope (criterion #3 six-field payload).

Preserves: actionId, robotId, skillId, idempotencyKey, paramsHash, payment.
"""
import hashlib
import json
import uuid


def compute_params_hash(params: dict) -> str:
    canonical = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskEnvelope:
    def __init__(self, action_id, robot_id, skill_id, params, payment, idempotency_key):
        self.action_id = action_id
        self.robot_id = robot_id
        self.skill_id = skill_id
        self.params = params or {}
        self.params_hash = compute_params_hash(self.params)
        self.payment = payment
        self.idempotency_key = idempotency_key

    @classmethod
    def from_request(cls, request: dict, payment=None):
        return cls(
            action_id=str(uuid.uuid4()),
            robot_id=request.get("robotId"),
            skill_id=request.get("skill"),
            params=request.get("params", {}),
            payment=payment if payment is not None else request.get("payment"),
            idempotency_key=request.get("idempotencyKey"),
        )

    def to_dict(self) -> dict:
        return {
            "actionId": self.action_id,
            "robotId": self.robot_id,
            "skillId": self.skill_id,
            "paramsHash": self.params_hash,
            "payment": self.payment,
            "idempotencyKey": self.idempotency_key,
        }

    def to_action_dict(self) -> dict:
        """Action envelope published to robot/tunnel/action.

        Keeps the six required fields (actionId, robotId, skillId, paramsHash,
        payment, idempotencyKey) and appends `params` so the robot knows what
        to execute. paramsHash lets the receiver verify params integrity.
        """
        d = self.to_dict()
        d["params"] = self.params
        return d
