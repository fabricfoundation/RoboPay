"""
LimX TRON1 — RoboPay Zenoh bridge (Tier 1, simulator-only).

Subscribes to robot/tunnel/action, validates and de-duplicates the envelope,
drives the MuJoCo TRON1 obstacle-navigation episode, and publishes a
correlated terminal result on robot/tunnel/result. Settlement is never
performed here — this bridge only reports status; the relay decides
settlement based on the terminal result.

Fail-closed: any malformed/unverified/duplicate action is rejected BEFORE
the simulator is touched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("tron1_robopay_bridge")

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
SKILL_ID = "tron1_obstacle_navigation"

REQUIRED_ACTION_FIELDS = [
    "actionId",
    "robotId",
    "skillId",
    "params",
    "paramsHash",
    "idempotencyKey",
    "payment",
]


class RejectReason:
    MALFORMED = "malformed_envelope"
    WRONG_ROBOT = "wrong_robot_id"
    UNKNOWN_SKILL = "unknown_skill_id"
    BAD_PARAMS_HASH = "invalid_params_hash"
    PAYMENT_UNVERIFIED = "payment_unverified"
    PAYMENT_EXPIRED = "payment_expired"
    DUPLICATE = "duplicate_action"


@dataclass
class ActionEnvelope:
    actionId: str
    robotId: str
    skillId: str
    params: dict
    paramsHash: str
    idempotencyKey: str
    payment: dict

    @staticmethod
    def parse(raw: dict) -> "ActionEnvelope":
        missing = [f for f in REQUIRED_ACTION_FIELDS if f not in raw]
        if missing:
            raise ValueError(f"{RejectReason.MALFORMED}: missing {missing}")
        return ActionEnvelope(
            actionId=str(raw["actionId"]),
            robotId=str(raw["robotId"]),
            skillId=str(raw["skillId"]),
            params=dict(raw["params"]),
            paramsHash=str(raw["paramsHash"]),
            idempotencyKey=str(raw["idempotencyKey"]),
            payment=dict(raw["payment"]),
        )


@dataclass
class ActionResult:
    actionId: str
    robotId: str
    skillId: str
    idempotencyKey: str
    paramsHash: str
    status: str  # "success" | "error" | "rejected"
    settlementEligible: bool
    reason: Optional[str] = None
    metrics: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "actionId": self.actionId,
                "robotId": self.robotId,
                "skillId": self.skillId,
                "idempotencyKey": self.idempotencyKey,
                "paramsHash": self.paramsHash,
                "status": self.status,
                "settlementEligible": self.settlementEligible,
                "reason": self.reason,
                "metrics": self.metrics,
            },
            sort_keys=True,
        )


def canonical_params_hash(params: dict) -> str:
    """sha256 of UTF-8 canonical JSON with sorted keys and compact separators."""
    blob = json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ReplayStore:
    """SQLite-backed idempotency/replay guard (ROBOPAY_STATE_DB)."""

    def __init__(self, db_path: Optional[str] = None):
        db_path = db_path or os.environ.get("ROBOPAY_STATE_DB", ":memory:")
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS claimed_actions (
                action_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL,
                authorization_id TEXT,
                claimed_at REAL NOT NULL
            )
            """
        )
        self._conn.commit()

    def try_claim(self, action_id: str, idempotency_key: str, authorization_id: str) -> bool:
        """Atomically claim actionId. Returns False if already claimed (replay)."""
        try:
            self._conn.execute(
                "INSERT INTO claimed_actions (action_id, idempotency_key, authorization_id, claimed_at) "
                "VALUES (?, ?, ?, ?)",
                (action_id, idempotency_key, authorization_id, time.time()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def close(self) -> None:
        self._conn.close()


class Tron1RoboPayBridge:
    """
    Fail-closed bridge: robot/tunnel/action -> validate -> MuJoCo episode
    -> robot/tunnel/result. No settlement logic lives here.
    """

    def __init__(self, robot_id: str, runner, replay_store: Optional[ReplayStore] = None):
        self.robot_id = robot_id
        self.runner = runner  # object exposing .run_episode(params) -> dict metrics
        self.replay_store = replay_store or ReplayStore()

    # ---- validation (all reject paths run BEFORE any simulator call) ----

    def _validate(self, env: ActionEnvelope) -> Optional[str]:
        if env.robotId != self.robot_id:
            return RejectReason.WRONG_ROBOT
        if env.skillId != SKILL_ID:
            return RejectReason.UNKNOWN_SKILL
        if canonical_params_hash(env.params) != env.paramsHash:
            return RejectReason.BAD_PARAMS_HASH

        payment = env.payment
        if not payment.get("verified") and payment.get("status") != "verified":
            return RejectReason.PAYMENT_UNVERIFIED
        expires_at = payment.get("expiresAt")
        if expires_at is not None and float(expires_at) < time.time():
            return RejectReason.PAYMENT_EXPIRED

        authorization_id = payment.get("authorizationId", env.idempotencyKey)
        claimed = self.replay_store.try_claim(env.actionId, env.idempotencyKey, authorization_id)
        if not claimed:
            return RejectReason.DUPLICATE

        return None

    def handle_raw_action(self, raw: dict) -> ActionResult:
        try:
            env = ActionEnvelope.parse(raw)
        except ValueError:
            return ActionResult(
                actionId=str(raw.get("actionId", "unknown")),
                robotId=str(raw.get("robotId", "unknown")),
                skillId=str(raw.get("skillId", "unknown")),
                idempotencyKey=str(raw.get("idempotencyKey", "unknown")),
                paramsHash=str(raw.get("paramsHash", "unknown")),
                status="rejected",
                settlementEligible=False,
                reason=RejectReason.MALFORMED,
            )

        reject_reason = self._validate(env)
        if reject_reason is not None:
            logger.info("rejecting action %s: %s", env.actionId, reject_reason)
            return ActionResult(
                actionId=env.actionId,
                robotId=env.robotId,
                skillId=env.skillId,
                idempotencyKey=env.idempotencyKey,
                paramsHash=env.paramsHash,
                status="rejected",
                settlementEligible=False,
                reason=reject_reason,
            )

        # Only now — after full validation and successful replay claim — do we
        # touch the simulator.
        try:
            metrics = self.runner.run_episode(env.params)
        except Exception as exc:  # noqa: BLE001
            logger.exception("simulator episode raised for action %s", env.actionId)
            return ActionResult(
                actionId=env.actionId,
                robotId=env.robotId,
                skillId=env.skillId,
                idempotencyKey=env.idempotencyKey,
                paramsHash=env.paramsHash,
                status="error",
                settlementEligible=False,
                reason=f"simulator_exception:{exc.__class__.__name__}",
            )

        episode_status = metrics.get("status")
        if episode_status == "goal_reached" and metrics.get("collisions", 1) == 0:
            return ActionResult(
                actionId=env.actionId,
                robotId=env.robotId,
                skillId=env.skillId,
                idempotencyKey=env.idempotencyKey,
                paramsHash=env.paramsHash,
                status="success",
                settlementEligible=True,
                metrics=metrics,
            )

        # collision, timeout, running-truncated, invalid_scene, etc. -> never settle
        return ActionResult(
            actionId=env.actionId,
            robotId=env.robotId,
            skillId=env.skillId,
            idempotencyKey=env.idempotencyKey,
            paramsHash=env.paramsHash,
            status="error",
            settlementEligible=False,
            reason=f"episode_status:{episode_status}",
            metrics=metrics,
        )

    def handle_stop(self, action_id: str) -> ActionResult:
        """Stop/cancel requires no payment and always succeeds immediately."""
        self.runner.stop()
        return ActionResult(
            actionId=action_id,
            robotId=self.robot_id,
            skillId=SKILL_ID,
            idempotencyKey=action_id,
            paramsHash="",
            status="success",
            settlementEligible=False,  # stop is never a paid, settleable action
            reason="stopped",
        )


def publish_result(zenoh_session, result: ActionResult) -> None:
    """Publish the terminal result to robot/tunnel/result."""
    zenoh_session.put(RESULT_TOPIC, result.to_json())


def main() -> None:  # pragma: no cover - wiring only, exercised via tests/mocks
    import zenoh  # type: ignore

    logging.basicConfig(level=logging.INFO)
    robot_id = os.environ["ROBOPAY_ROBOT_ID"]

    from simulation.runners.tron1_runner import Tron1MuJoCoRunner

    runner = Tron1MuJoCoRunner(scene_path=os.environ.get(
        "TRON1_SCENE", "simulation/scenes/tron1.xml"
    ))
    bridge = Tron1RoboPayBridge(robot_id=robot_id, runner=runner)

    with zenoh.open(zenoh.Config()) as session:
        def on_action(sample) -> None:
            raw = json.loads(bytes(sample.payload).decode("utf-8"))
            result = bridge.handle_raw_action(raw)
            publish_result(session, result)

        session.declare_subscriber(ACTION_TOPIC, on_action)
        logger.info("tron1_robopay_bridge listening on %s", ACTION_TOPIC)
        while True:
            time.sleep(1.0)


if __name__ == "__main__":  # pragma: no cover
    main()
