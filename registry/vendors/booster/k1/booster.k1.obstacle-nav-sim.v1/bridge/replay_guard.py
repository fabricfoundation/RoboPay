"""SQLite-backed replay guard: idempotencyKey, actionId, and
payment.authorizationId are each unique. A repeat of any one blocks
execution before the simulator is ever dispatched."""
import sqlite3
import threading
from dataclasses import dataclass


class ReplayDetected(Exception):
    """Carries the original recorded fingerprint alongside the new one."""
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
    """One instance per bridge process. Lock-guarded since sqlite3
    connections aren't thread-safe on their own."""

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
        """Must be called BEFORE dispatching to the simulator. Raises
        ReplayDetected if idempotencyKey, actionId, or authorizationId
        was already used."""
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

            # Reserve now, before dispatch, so a concurrent duplicate sees this row.
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
