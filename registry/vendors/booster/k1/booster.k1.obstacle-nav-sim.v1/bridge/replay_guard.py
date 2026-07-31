"""
Replay protection for Booster K1 action execution, per the
replayProtection block in execution-mapping.yaml:

  storage: sqlite
  key: idempotencyKey
  uniqueFields: [actionId, payment.authorizationId]
  fingerprintFields: [actionId, robotId, skillId, paramsHash, payment.authorizationId]

Guarantees: the same idempotencyKey, the same actionId, or the same
payment authorizationId can never trigger a second execution. If a
replay is detected, the caller must NOT re-dispatch to the simulator
and must NOT re-attempt settlement.
"""
import sqlite3
import threading
from dataclasses import dataclass


class ReplayDetected(Exception):
    """Raised when an action has already been recorded as executed.
    Carries the original recorded fingerprint so the caller can
    decide whether this is a legitimate retry (identical fingerprint)
    or a suspicious mismatch (same key, different content)."""
    def __init__(self, reason: str, original_fingerprint: str, new_fingerprint: str):
        self.reason = reason
        self.original_fingerprint = original_fingerprint
        self.new_fingerprint = new_fingerprint
        super().__init__(
            f"replay detected ({reason}): original={original_fingerprint!r} new={new_fingerprint!r}"
        )


@dataclass
class Fingerprint:
    action_id: str
    robot_id: str
    skill_id: str
    params_hash: str
    authorization_id: str

    def as_string(self) -> str:
        return "|".join([
            self.action_id, self.robot_id, self.skill_id,
            self.params_hash, self.authorization_id,
        ])


class ReplayGuard:
    """Thread-safe SQLite-backed replay guard. One instance per bridge
    process; safe to share across the async event loop via a lock
    because sqlite3 connections are not otherwise thread-safe."""

    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS executed_actions (
                    idempotency_key TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL UNIQUE,
                    authorization_id TEXT NOT NULL UNIQUE,
                    fingerprint TEXT NOT NULL,
                    result_status TEXT,
                    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            self._conn.commit()

    def check_and_reserve(self, idempotency_key: str, fp: Fingerprint) -> None:
        """Atomically checks whether this action has been seen before
        (by idempotencyKey, actionId, or authorizationId) and, if not,
        reserves the slot so a concurrent duplicate request is also
        rejected. Raises ReplayDetected if any of the three unique
        keys already exists.

        Must be called BEFORE dispatching to the simulator."""
        fingerprint_str = fp.as_string()
        with self._lock:
            cur = self._conn.execute(
                "SELECT idempotency_key, action_id, authorization_id, fingerprint "
                "FROM executed_actions WHERE idempotency_key = ? OR action_id = ? OR authorization_id = ?",
                (idempotency_key, fp.action_id, fp.authorization_id),
            )
            row = cur.fetchone()
            if row is not None:
                existing_key, existing_action, existing_auth, existing_fp = row
                if existing_key == idempotency_key:
                    reason = "idempotencyKey already used"
                elif existing_action == fp.action_id:
                    reason = "actionId already used"
                else:
                    reason = "payment.authorizationId already used"
                raise ReplayDetected(reason, existing_fp, fingerprint_str)

            # Reserve the slot immediately (result_status set later via
            # record_result) so a concurrent duplicate sees this row.
            self._conn.execute(
                "INSERT INTO executed_actions "
                "(idempotency_key, action_id, authorization_id, fingerprint, result_status) "
                "VALUES (?, ?, ?, ?, NULL)",
                (idempotency_key, fp.action_id, fp.authorization_id, fingerprint_str),
            )
            self._conn.commit()

    def record_result(self, idempotency_key: str, result_status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE executed_actions SET result_status = ? WHERE idempotency_key = ?",
                (result_status, idempotency_key),
            )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
