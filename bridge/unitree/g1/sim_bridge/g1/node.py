"""Executing a validated action, and deciding whether it may be paid for.

The settlement decision lives here, in one place, and it is deliberately
boring: `settle` is true only when the robot reported success. Failure,
timeout, rejection and crash all leave it false. The bounty criteria state the
rule directly -- "If the robot action fails, times out, or returns an error,
the relay must not settle the payment" -- and a submission that settles after
a failed execution is listed as non-acceptable, so this is the single most
important line in the file.

Replay defence lives here too. A repeated idempotency key returns the stored
outcome of the first attempt without touching the simulator, so paying once
and replaying the message cannot make the robot move twice.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..simulation.metrics import RunMetrics
from .action_contract import ActionEnvelope, ActionRejected, RejectionCode
from .mapper import TaskSpec, resolve


@dataclass
class ExecutionResult:
    """The outcome of one action, in the shape the criteria prescribe."""

    status: str  # "success" | "error"
    skill: str
    action_id: str
    settle: bool
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    replayed: bool = False

    @classmethod
    def success(
        cls,
        action_id: str,
        skill: str,
        message: str,
        metrics: dict[str, Any],
    ) -> "ExecutionResult":
        return cls(
            status="success",
            skill=skill,
            action_id=action_id,
            settle=True,
            result={"message": message},
            metrics=metrics,
        )

    @classmethod
    def failure(
        cls,
        action_id: str,
        skill: str,
        code: str,
        message: str,
        metrics: dict[str, Any] | None = None,
    ) -> "ExecutionResult":
        return cls(
            status="error",
            skill=skill,
            action_id=action_id,
            # The whole point: a failed action is never settleable.
            settle=False,
            error={"code": code, "message": message},
            metrics=metrics or {},
        )

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "skill": self.skill,
            "actionId": self.action_id,
            "settle": self.settle,
        }
        if self.result is not None:
            out["result"] = self.result
        if self.error is not None:
            out["error"] = self.error
        if self.metrics:
            out["metrics"] = self.metrics
        if self.replayed:
            out["replayed"] = True
        return out


class IdempotencyStore:
    """Remembers what each idempotency key produced.

    Bounded and time-limited so a long-running bridge cannot grow without end.
    """

    def __init__(self, ttl_seconds: float = 900.0, capacity: int = 512) -> None:
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._entries: dict[str, tuple[float, ExecutionResult]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> ExecutionResult | None:
        with self._lock:
            self._evict()
            found = self._entries.get(key)
            return found[1] if found else None

    def put(self, key: str, result: ExecutionResult) -> None:
        with self._lock:
            self._evict()
            if len(self._entries) >= self._capacity:
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (time.monotonic(), result)

    def _evict(self) -> None:
        cutoff = time.monotonic() - self._ttl
        for key in [k for k, (t, _) in self._entries.items() if t < cutoff]:
            self._entries.pop(key, None)


#: Runs a task and reports how it went. Injected so the Zenoh bridge can be
#: tested without a simulator, and so the simulator can be exercised without
#: Zenoh.
Runner = Callable[[TaskSpec], RunMetrics]


class ActionNode:
    """Turns validated envelopes into robot motion and settleable results."""

    def __init__(
        self,
        robot_id: str,
        runner: Runner,
        store: IdempotencyStore | None = None,
    ) -> None:
        self.robot_id = robot_id
        self._runner = runner
        self._store = store or IdempotencyStore()

    def handle(self, envelope: ActionEnvelope) -> ExecutionResult:
        """Validate, deduplicate, execute. Never raises for a bad request."""
        try:
            envelope.require_robot(self.robot_id)
            envelope.require_unexpired()
            envelope.require_untampered_params()
            task = resolve(envelope)
            if task.skill_id != "stop":
                envelope.require_verified_payment()
        except ActionRejected as rejected:
            return ExecutionResult.failure(
                envelope.action_id, envelope.skill_id, rejected.code, rejected.message
            )

        cached = self._store.get(envelope.idempotency_key)
        if cached is not None:
            # Do not re-run, and do not settle a second time.
            replay = ExecutionResult(
                status=cached.status,
                skill=cached.skill,
                action_id=envelope.action_id,
                settle=False,
                result=cached.result,
                error=cached.error or {
                    "code": RejectionCode.REPLAYED,
                    "message": "idempotency key already executed",
                },
                metrics=cached.metrics,
                replayed=True,
            )
            return replay

        result = self._execute(envelope, task)
        self._store.put(envelope.idempotency_key, result)
        return result

    def _execute(self, envelope: ActionEnvelope, task: TaskSpec) -> ExecutionResult:
        if task.skill_id == "stop":
            return ExecutionResult.success(
                envelope.action_id, task.skill_id, "Robot stopped", {}
            )
        if task.expect_failure:
            return ExecutionResult.failure(
                envelope.action_id,
                task.skill_id,
                "ACTION_FAILED",
                "diagnostic_fail always fails; payment must not settle",
            )
        try:
            metrics = self._runner(task)
        except Exception as exc:  # noqa: BLE001 - a crash must not settle either
            return ExecutionResult.failure(
                envelope.action_id,
                task.skill_id,
                "ACTION_FAILED",
                f"simulator raised {type(exc).__name__}: {exc}",
            )
        if not metrics.success:
            return ExecutionResult.failure(
                envelope.action_id,
                task.skill_id,
                "ACTION_FAILED",
                metrics.reason or "robot failed to complete the action",
                metrics.to_json(),
            )
        return ExecutionResult.success(
            envelope.action_id,
            task.skill_id,
            "Action completed",
            metrics.to_json(),
        )
