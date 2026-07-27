"""Action/result envelope schema for the sim-arm-01 pay-to-actuate flow.

The envelope carries every field required to preserve payment safety end-to-end:
actionId, robotId, skillId, idempotencyKey, paramsHash, payment. The result is
correlated back to its request by actionId.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict


# Documented Zenoh topics (RoboPay convention).
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"


def params_hash(params: dict) -> str:
    """Stable hash of the action params, used to detect tampering."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class ActionEnvelope:
    robotId: str
    skillId: str
    params: dict
    payment: dict                      # verified receipt / txHash
    actionId: str = field(default_factory=lambda: str(uuid.uuid4()))
    idempotencyKey: str = field(default_factory=lambda: str(uuid.uuid4()))
    paramsHash: str = ""

    def __post_init__(self):
        if not self.paramsHash:
            self.paramsHash = params_hash(self.params)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "ActionEnvelope":
        return ActionEnvelope(**json.loads(raw))


@dataclass
class ResultEnvelope:
    """Async execution result, correlated back to the request by actionId."""
    actionId: str
    robotId: str
    skillId: str
    status: str                        # "success" | "error"
    metrics: dict = field(default_factory=dict)   # simulator state metrics
    code: str = ""                     # e.g. "ACTION_FAILED" on error
    message: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "ResultEnvelope":
        return ResultEnvelope(**json.loads(raw))

    @staticmethod
    def success(action: "ActionEnvelope", metrics: dict) -> "ResultEnvelope":
        return ResultEnvelope(
            actionId=action.actionId, robotId=action.robotId,
            skillId=action.skillId, status="success", metrics=metrics,
        )

    @staticmethod
    def error(action: "ActionEnvelope", code: str, message: str,
              metrics: dict = None) -> "ResultEnvelope":
        return ResultEnvelope(
            actionId=action.actionId, robotId=action.robotId,
            skillId=action.skillId, status="error", code=code,
            message=message, metrics=metrics or {},
        )
