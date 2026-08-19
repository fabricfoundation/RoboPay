"""Durable idempotency for payment-validated actions.

A payment-validated action must actuate the robot **once**. Replay protection on the payment
alone is not enough: the same idempotency key can arrive with a different
payment, and an in-memory guard forgets everything the moment the bridge
restarts — which is exactly when a client is most likely to retry.

The store is keyed on the tunnel's own identity for the request::

    robot_id + skill_id + idempotency_key

and additionally records what that key was first used for, together with how
that first action ended. A repeat of the same key is answered with that recorded
outcome instead of moving the robot; a repeat that changes the parameters or
arrives with a different payment is refused outright, because it is no longer
the same request.

Records are appended to a JSON-lines file and reloaded on start, so the
guarantee survives a restart.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_STORE_PATH = Path(".robopay/atlas-idempotency.jsonl")


@dataclass(frozen=True)
class ActionRecord:
    """What a given idempotency key was first used for, and how it ended."""

    robot_id: str
    skill_id: str
    idempotency_key: str
    params_hash: str
    payment_fingerprint: str
    action_id: str
    status: str

    @property
    def key(self) -> str:
        return f"{self.robot_id}|{self.skill_id}|{self.idempotency_key}"


class ConflictingRequest(ValueError):
    """The same idempotency key arrived describing a different request."""

    def __init__(self, message: str, code: str = "IDEMPOTENCY_CONFLICT") -> None:
        super().__init__(message)
        self.code = code


class IdempotencyStore:
    """File-backed store of which actions have already actuated."""

    def __init__(self, path: Path | str | None = DEFAULT_STORE_PATH) -> None:
        self.path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._records: dict[str, ActionRecord] = {}
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = ActionRecord(**json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue  # a truncated tail must not take the bridge down
            self._records[record.key] = record

    def _append(self, record: ActionRecord) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")

    # -- guard --------------------------------------------------------------
    def claim(
        self,
        robot_id: str,
        skill_id: str,
        idempotency_key: str,
        params_hash: str,
        payment_fingerprint: str,
        action_id: str,
    ) -> ActionRecord | None:
        """Atomically reserve this key, or report who already holds it.

        Checking and recording must happen under one lock: two concurrent
        requests carrying the same key would otherwise both see "new" and both
        actuate the robot. Returns ``None`` when the caller now owns the claim,
        or the existing record when someone else already does.
        """
        if not idempotency_key:
            return None
        key = f"{robot_id}|{skill_id}|{idempotency_key}"
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                self._assert_same_request(record, params_hash, payment_fingerprint)
                return record
            reservation = ActionRecord(
                robot_id=robot_id,
                skill_id=skill_id,
                idempotency_key=idempotency_key,
                params_hash=params_hash,
                payment_fingerprint=payment_fingerprint,
                action_id=action_id,
                status="accepted",
            )
            self._records[key] = reservation
            self._append(reservation)
        return None

    @staticmethod
    def _assert_same_request(
        record: ActionRecord, params_hash: str, payment_fingerprint: str
    ) -> None:
        if record.params_hash != params_hash:
            raise ConflictingRequest(
                f"idempotency key {record.idempotency_key!r} was already used with "
                "different parameters",
                code="IDEMPOTENCY_PARAMS_CONFLICT",
            )
        if record.payment_fingerprint != payment_fingerprint:
            raise ConflictingRequest(
                f"idempotency key {record.idempotency_key!r} was already used with a "
                "different payment",
                code="IDEMPOTENCY_PAYMENT_CONFLICT",
            )

    def check(
        self,
        robot_id: str,
        skill_id: str,
        idempotency_key: str,
        params_hash: str,
        payment_fingerprint: str,
    ) -> ActionRecord | None:
        """Return the earlier record for this key, or None if it is new.

        Raises :class:`ConflictingRequest` when the key was already used for a
        materially different request.
        """
        if not idempotency_key:
            return None
        key = f"{robot_id}|{skill_id}|{idempotency_key}"
        with self._lock:
            record = self._records.get(key)
        if record is None:
            return None
        self._assert_same_request(record, params_hash, payment_fingerprint)
        return record

    def remember(
        self,
        robot_id: str,
        skill_id: str,
        idempotency_key: str,
        params_hash: str,
        payment_fingerprint: str,
        action_id: str,
        status: str,
    ) -> ActionRecord | None:
        """Record that this key has actuated the robot."""
        if not idempotency_key:
            return None
        record = ActionRecord(
            robot_id=robot_id,
            skill_id=skill_id,
            idempotency_key=idempotency_key,
            params_hash=params_hash,
            payment_fingerprint=payment_fingerprint,
            action_id=action_id,
            status=status,
        )
        with self._lock:
            self._records[record.key] = record
            self._append(record)
        return record

    def complete(
        self, robot_id: str, skill_id: str, idempotency_key: str, status: str
    ) -> ActionRecord | None:
        """Record how the claimed action actually ended.

        Without this the store would only ever remember ``accepted``, and a
        duplicate could not be answered with the outcome of the run it repeats.
        """
        if not idempotency_key:
            return None
        key = f"{robot_id}|{skill_id}|{idempotency_key}"
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return None
            finished = ActionRecord(
                robot_id=record.robot_id,
                skill_id=record.skill_id,
                idempotency_key=record.idempotency_key,
                params_hash=record.params_hash,
                payment_fingerprint=record.payment_fingerprint,
                action_id=record.action_id,
                status=status,
            )
            self._records[key] = finished
            self._append(finished)
        return finished

    def __len__(self) -> int:
        return len(self._records)
