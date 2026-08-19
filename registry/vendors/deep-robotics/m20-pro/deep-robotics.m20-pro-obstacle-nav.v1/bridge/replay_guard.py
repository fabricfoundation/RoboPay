"""SQLite-backed replay guard: actionId is unique. A repeat blocks
execution before the simulator is ever dispatched.

The tunnel already enforces its own idempotency (tunnel/internal/handlers
/idempotency.go, keyed by actionId, durable across restarts) before an
action is ever published to Zenoh. This guard is a second, independent
check at the bridge -- it exists so that even if the tunnel's guarantee
were ever bypassed (e.g. a manually crafted Zenoh publish during testing),
the simulator itself is never dispatched twice for the same actionId."""
import sqlite3
import threading
from dataclasses import dataclass


class ReplayDetected(Exception):
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

    def as_string(self) -> str:
        return "|".join([self.action_id, self.robot_id, self.skill_id, self.params_hash])


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
                    action_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    result_status TEXT,
                    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            self._conn.commit()

    def check_and_reserve(self, fp: Fingerprint) -> None:
        """Must be called BEFORE dispatching to the simulator. Raises
        ReplayDetected if actionId was already used."""
        fingerprint_str = fp.as_string()
        with self._lock:
            cur = self._conn.execute(
                "SELECT fingerprint FROM executed_actions WHERE action_id = ?",
                (fp.action_id,),
            )
            row = cur.fetchone()
            if row is not None:
                raise ReplayDetected("actionId already used", row[0], fingerprint_str)

            self._conn.execute(
                "INSERT INTO executed_actions (action_id, fingerprint, result_status) "
                "VALUES (?, ?, NULL)",
                (fp.action_id, fingerprint_str),
            )
            self._conn.commit()

    def record_result(self, action_id: str, result_status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE executed_actions SET result_status = ? WHERE action_id = ?",
                (result_status, action_id),
            )
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
