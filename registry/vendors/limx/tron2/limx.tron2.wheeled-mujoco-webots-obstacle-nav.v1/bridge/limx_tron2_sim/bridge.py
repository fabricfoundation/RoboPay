"""Profile-scoped, fail-closed Zenoh bridge for LimX TRON 2 simulation.

The shared Tunnel verifies x402.  This second boundary accepts only Tunnel
events that preserve the complete action/payment correlation tuple.  It never
verifies or settles a payment locally; the Tunnel may settle only after the
same action ID returns terminal ``success``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import PROFILE_ID, ROBOT_ID, ContractError, NavigationRequest, validate_action
from .runtime import run_mujoco_episode


ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/limx_tron2/metrics"
READY_TOPIC = "robot/limx_tron2/ready"
PROFILE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPLAY_DB = PROFILE_ROOT / "artifacts" / "state" / "limx_tron2_replay.sqlite3"


def production_episode_runner(request: NavigationRequest) -> dict[str, Any]:
    visual = os.environ.get("LIMX_TRON2_MUJOCO_VIEWER", "").strip().lower() in {"1", "true", "yes"}
    hold_seconds = float(os.environ.get("LIMX_TRON2_MUJOCO_VIEWER_HOLD_SECONDS", "180")) if visual else 0.0
    return run_mujoco_episode(request, viewer=visual, viewer_hold_seconds=max(0.0, hold_seconds))


class EventContractError(ValueError):
    """An action event is not a trusted, correlated Tunnel action."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as error:
        raise EventContractError("event contains a non-JSON value") from error


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TunnelActionEvent:
    action_id: str
    robot_id: str
    skill_id: str
    action: str
    params: dict[str, Any]
    params_hash: str
    idempotency_key: str
    payment_fingerprint: str
    timestamp: str


def parse_tunnel_action(raw: bytes) -> TunnelActionEvent:
    """Reject legacy/bare commands and parse the post-verification event."""

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EventContractError("action event is not valid UTF-8 JSON") from error
    if not isinstance(envelope, dict):
        raise EventContractError("action event must be an object")
    payload = envelope.get("payload")
    transaction = envelope.get("transaction_details")
    if not isinstance(payload, dict):
        raise EventContractError("action event payload must be an object")
    if not isinstance(transaction, dict):
        raise EventContractError("missing transaction details")
    if transaction.get("payment_payload") is None or transaction.get("payment_requirements") is None:
        raise EventContractError("missing Tunnel-verified payment evidence")

    def required_string(name: str) -> str:
        value = envelope.get(name)
        if not isinstance(value, str) or not value.strip():
            raise EventContractError(f"missing {name}")
        return value.strip()

    action_id = required_string("action_id")
    robot_id = required_string("robot_id")
    skill_id = required_string("skill_id")
    params_hash = required_string("params_hash")
    idempotency_key = required_string("idempotency_key")
    action = payload.get("action")
    params = payload.get("params", {})
    if not isinstance(action, str) or not action.strip():
        raise EventContractError("payload action is missing")
    if not isinstance(params, dict):
        raise EventContractError("payload params must be an object")
    if action.strip() != skill_id:
        raise EventContractError("payload action and correlated skill_id differ")
    if params_hash != _hash(params):
        raise EventContractError("params_hash does not match payload params")
    return TunnelActionEvent(
        action_id=action_id,
        robot_id=robot_id,
        skill_id=skill_id,
        action=action.strip(),
        params=params,
        params_hash=params_hash,
        idempotency_key=idempotency_key,
        payment_fingerprint=_hash(transaction["payment_payload"]),
        timestamp=str(envelope.get("timestamp", "")),
    )


class DurableReplayStore:
    """SQLite-backed payment-bound idempotency retained across restarts."""

    def __init__(self, path: Path | str = DEFAULT_REPLAY_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS executed_actions (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payment_fingerprint TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    updated_unix_ms INTEGER NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def reserve(self, event: TunnelActionEvent) -> str | None:
        now = time.time_ns() // 1_000_000
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM executed_actions WHERE idempotency_key = ?", (event.idempotency_key,)
            ).fetchone():
                return "REPLAY_DETECTED"
            if connection.execute(
                "SELECT 1 FROM executed_actions WHERE payment_fingerprint = ?", (event.payment_fingerprint,)
            ).fetchone():
                return "PAYMENT_REPLAY_DETECTED"
            if connection.execute(
                "SELECT 1 FROM executed_actions WHERE action_id = ?", (event.action_id,)
            ).fetchone():
                return "REPLAY_DETECTED"
            connection.execute(
                """INSERT INTO executed_actions
                   (action_id, idempotency_key, payment_fingerprint, status, updated_unix_ms)
                   VALUES (?, ?, ?, 'reserved', ?)""",
                (event.action_id, event.idempotency_key, event.payment_fingerprint, now),
            )
        return None

    def record_terminal(self, event: TunnelActionEvent, status: str, result: dict[str, Any]) -> None:
        if status not in {"success", "failure"}:
            raise ValueError("terminal status must be success or failure")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE executed_actions SET status = ?, result_json = ?, updated_unix_ms = ?
                   WHERE action_id = ?""",
                (status, _canonical_json(result), time.time_ns() // 1_000_000, event.action_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("cannot record unreserved action")

    def action_count(self) -> int:
        with self._lock, self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM executed_actions").fetchone()[0])


def _terminal(event: TunnelActionEvent, status: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": event.action_id,
        "robot_id": event.robot_id,
        "skill_id": event.skill_id,
        "params_hash": event.params_hash,
        "idempotency_key": event.idempotency_key,
        "profile_id": PROFILE_ID,
        "status": status,
        "result": result,
    }


class LimXTron2Execution:
    """Deterministic execution boundary, independent of Zenoh plumbing."""

    def __init__(
        self,
        *,
        replay_store: DurableReplayStore | None = None,
        episode_runner: Callable[[NavigationRequest], dict[str, Any]] = production_episode_runner,
    ):
        self.replay_store = replay_store or DurableReplayStore()
        self.episode_runner = episode_runner

    def process(self, raw: bytes) -> dict[str, Any] | None:
        try:
            event = parse_tunnel_action(raw)
        except EventContractError:
            return None
        try:
            request = validate_action(event.robot_id, event.action, event.skill_id, event.params)
        except ContractError as error:
            return _terminal(
                event,
                "failure",
                {"success": False, "error_code": "ACTION_CONTRACT_REJECTED", "message": str(error)},
            )
        replay_error = self.replay_store.reserve(event)
        if replay_error:
            return _terminal(event, "failure", {"success": False, "error_code": replay_error})
        try:
            result = self.episode_runner(request)
        except Exception as error:
            result = {"success": False, "error_code": "SIMULATOR_EXECUTION_ERROR", "message": str(error)}
        status = "success" if result.get("success") else "failure"
        self.replay_store.record_terminal(event, status, result)
        return _terminal(event, status, result)


@dataclass(frozen=True)
class BridgeSettings:
    zenoh_endpoint: str | None
    zenoh_config_path: str | None
    action_topic: str
    result_topic: str
    metrics_topic: str
    ready_topic: str = READY_TOPIC

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        def configured(name: str, default: str) -> str:
            return os.environ.get(name, default).strip() or default

        return cls(
            zenoh_endpoint=os.environ.get("ZENOH_ENDPOINT", "").strip() or None,
            zenoh_config_path=os.environ.get("ZENOH_CONFIG", "").strip() or None,
            action_topic=configured("ZENOH_ACTION_TOPIC", ACTION_TOPIC),
            result_topic=configured("ZENOH_RESULT_TOPIC", RESULT_TOPIC),
            metrics_topic=configured("ZENOH_METRICS_TOPIC", METRICS_TOPIC),
            ready_topic=configured("ZENOH_READY_TOPIC", READY_TOPIC),
        )


class LimXTron2ZenohBridge:
    """Subscribe to the private Tunnel action topic and publish terminal data."""

    def __init__(self, settings: BridgeSettings | None = None, execution: LimXTron2Execution | None = None):
        self.settings = settings or BridgeSettings.from_env()
        self.execution = execution or LimXTron2Execution()
        self._session = self._open_session()
        self._results = self._session.declare_publisher(self.settings.result_topic)
        self._metrics = self._session.declare_publisher(self.settings.metrics_topic)
        self._subscriber = self._session.declare_subscriber(self.settings.action_topic, self._on_sample)
        self._ready = self._session.declare_publisher(self.settings.ready_topic)
        self._ready.put(
            _canonical_json(
                {
                    "status": "ready",
                    "profile_id": PROFILE_ID,
                    "robot_id": ROBOT_ID,
                    "action_topic": self.settings.action_topic,
                    "result_topic": self.settings.result_topic,
                }
            ).encode("utf-8")
        )

    def _open_session(self):
        import zenoh

        if self.settings.zenoh_config_path:
            return zenoh.open(zenoh.Config.from_file(self.settings.zenoh_config_path))
        if self.settings.zenoh_endpoint:
            return zenoh.open(
                zenoh.Config.from_json5(
                    _canonical_json({"mode": "client", "connect": {"endpoints": [self.settings.zenoh_endpoint]}})
                )
            )
        raise RuntimeError(
            "Refusing an implicit Zenoh session. Set ZENOH_CONFIG for the private Tunnel boundary "
            "(or ZENOH_ENDPOINT only in a controlled local integration test)."
        )

    def _on_sample(self, sample) -> None:
        result = self.execution.process(bytes(sample.payload.to_bytes()))
        if result is None:
            return
        payload = _canonical_json(result).encode("utf-8")
        self._metrics.put(payload)
        self._results.put(payload)

    def close(self) -> None:
        self._subscriber.undeclare()
        self._results.undeclare()
        self._metrics.undeclare()
        self._ready.undeclare()
        self._session.close()

    def spin(self) -> None:  # pragma: no cover - process lifecycle
        try:
            while True:
                time.sleep(0.2)
        finally:
            self.close()


def main() -> None:  # pragma: no cover - process lifecycle
    LimXTron2ZenohBridge().spin()


if __name__ == "__main__":
    main()
