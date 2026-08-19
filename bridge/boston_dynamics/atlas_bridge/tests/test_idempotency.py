"""A paid action must actuate the robot exactly once — including after a restart.

Replay protection on the payment is not the same guarantee: the same
idempotency key can arrive carrying a different payment, and an in-memory guard
forgets everything precisely when a client is most likely to retry.
"""

from __future__ import annotations

import json

import pytest

from bridge.boston_dynamics.atlas_bridge.bridge import AtlasActionHandler
from bridge.boston_dynamics.atlas_bridge.idempotency import (
    ConflictingRequest,
    IdempotencyStore,
)

ROBOT = "atlas-sim-01"
SKILL = "inspect_shelf"


def envelope(
    action_id: str = "act-1",
    idempotency_key: str = "idem-1",
    params: dict | None = None,
    payment: dict | None = None,
) -> bytes:
    return json.dumps({
        "payload": {
            "action": SKILL,
            "skill_id": SKILL,
            "robot_id": ROBOT,
            "action_id": action_id,
            "idempotency_key": idempotency_key,
            "params": params if params is not None else {"maxDurationSec": 10},
        },
        "transaction_details": {"payment_payload": payment or {"txHash": "0x" + "a" * 64}},
        "timestamp": "2026-08-19T00:00:00Z",
    }).encode("utf-8")


class Recorder:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def __call__(self, payload: bytes) -> None:
        self.messages.append(json.loads(payload.decode("utf-8")))

    @property
    def last(self) -> dict:
        assert self.messages
        return self.messages[-1]


def build(store: IdempotencyStore) -> tuple[AtlasActionHandler, Recorder, list]:
    executions: list = []
    recorder = Recorder()

    def execute(max_duration_seconds: float, stop_requested=None) -> dict:
        executions.append(max_duration_seconds)
        return {"success": True, "status": "success", "targets_completed": 3, "targets_total": 3}

    handler = AtlasActionHandler(
        recorder, execute=execute, synchronous=True, idempotency=store
    )
    return handler, recorder, executions


# -- the core guarantee -----------------------------------------------------
def test_same_key_actuates_the_robot_once():
    handler, recorder, executions = build(IdempotencyStore(path=None))

    assert handler.handle(envelope(action_id="act-1")) == "executed"
    assert handler.handle(envelope(action_id="act-2")) == "duplicate"

    assert len(executions) == 1, "the robot moved twice for one idempotency key"
    assert recorder.last["result"]["error_code"] == "DUPLICATE_ACTION"
    assert recorder.last["result"]["first_action_id"] == "act-1"


def test_guarantee_survives_a_restart(tmp_path):
    """The store is reloaded from disk, so a retry after a crash is still one."""
    path = tmp_path / "idempotency.jsonl"

    first, _, first_runs = build(IdempotencyStore(path=path))
    assert first.handle(envelope(action_id="act-1")) == "executed"
    assert len(first_runs) == 1

    # A brand-new handler, as if the bridge had been restarted.
    second, recorder, second_runs = build(IdempotencyStore(path=path))
    assert second.handle(envelope(action_id="act-2")) == "duplicate"
    assert second_runs == [], "the robot moved again after a restart"
    assert recorder.last["result"]["first_action_id"] == "act-1"


def test_different_keys_each_actuate():
    handler, _, executions = build(IdempotencyStore(path=None))
    assert handler.handle(envelope(action_id="act-1", idempotency_key="idem-1")) == "executed"
    assert handler.handle(envelope(action_id="act-2", idempotency_key="idem-2")) == "executed"
    assert len(executions) == 2


# -- conflicts are refused, not silently replayed ---------------------------
def test_same_key_with_different_parameters_is_refused():
    handler, recorder, executions = build(IdempotencyStore(path=None))
    assert handler.handle(envelope(params={"maxDurationSec": 10})) == "executed"
    assert handler.handle(envelope(action_id="act-2", params={"maxDurationSec": 20})) == "failure"
    assert len(executions) == 1
    assert recorder.last["result"]["error_code"] == "IDEMPOTENCY_PARAMS_CONFLICT"


def test_same_key_with_a_different_payment_is_refused():
    handler, recorder, executions = build(IdempotencyStore(path=None))
    assert handler.handle(envelope(payment={"txHash": "0x" + "a" * 64})) == "executed"
    assert handler.handle(
        envelope(action_id="act-2", payment={"txHash": "0x" + "b" * 64})
    ) == "failure"
    assert len(executions) == 1
    assert recorder.last["result"]["error_code"] == "IDEMPOTENCY_PAYMENT_CONFLICT"


# -- store behaviour --------------------------------------------------------
def test_store_raises_on_conflicting_parameters():
    store = IdempotencyStore(path=None)
    store.remember(ROBOT, SKILL, "idem-1", "hash-a", "pay-a", "act-1", "accepted")
    assert store.check(ROBOT, SKILL, "idem-1", "hash-a", "pay-a").action_id == "act-1"
    with pytest.raises(ConflictingRequest):
        store.check(ROBOT, SKILL, "idem-1", "hash-b", "pay-a")
    with pytest.raises(ConflictingRequest):
        store.check(ROBOT, SKILL, "idem-1", "hash-a", "pay-b")


def test_store_scopes_keys_per_robot_and_skill():
    store = IdempotencyStore(path=None)
    store.remember(ROBOT, SKILL, "idem-1", "h", "p", "act-1", "accepted")
    assert store.check("another-robot", SKILL, "idem-1", "h", "p") is None
    assert store.check(ROBOT, "stop", "idem-1", "h", "p") is None


def test_requests_without_a_key_are_not_deduplicated():
    """An absent idempotency key means the caller opted out; do not invent one."""
    store = IdempotencyStore(path=None)
    assert store.check(ROBOT, SKILL, "", "h", "p") is None
    assert store.remember(ROBOT, SKILL, "", "h", "p", "act-1", "accepted") is None


def test_a_truncated_store_does_not_break_startup(tmp_path):
    path = tmp_path / "idempotency.jsonl"
    path.write_text(
        json.dumps({
            "robot_id": ROBOT, "skill_id": SKILL, "idempotency_key": "idem-1",
            "params_hash": "h", "payment_fingerprint": "p",
            "action_id": "act-1", "status": "accepted",
        }) + "\n{ this line is truncated",
        encoding="utf-8",
    )
    store = IdempotencyStore(path=path)
    assert len(store) == 1


def test_concurrent_duplicates_actuate_once():
    """Two threads racing with the same key must not both move the robot.

    A check-then-record guard passes every sequential test and still lets two
    simultaneous retries through, which is exactly when a client retries.
    """
    import threading

    store = IdempotencyStore(path=None)
    executions: list[float] = []
    barrier = threading.Barrier(8)
    lock = threading.Lock()

    def execute(max_duration_seconds: float, stop_requested=None) -> dict:
        with lock:
            executions.append(max_duration_seconds)
        return {"success": True, "status": "success", "targets_completed": 3, "targets_total": 3}

    def worker(index: int) -> None:
        recorder = Recorder()
        h = AtlasActionHandler(recorder, execute=execute, synchronous=True, idempotency=store)
        barrier.wait()
        h.handle(envelope(action_id=f"act-{index}", idempotency_key="idem-race"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(executions) == 1, f"the robot moved {len(executions)} times for one key"


def test_claim_is_atomic():
    store = IdempotencyStore(path=None)
    assert store.claim(ROBOT, SKILL, "idem-1", "h", "p", "act-1") is None
    held = store.claim(ROBOT, SKILL, "idem-1", "h", "p", "act-2")
    assert held is not None and held.action_id == "act-1"


def test_duplicate_is_answered_with_the_recorded_outcome():
    """A repeat must report how the original run ended, not just 'accepted'."""
    store = IdempotencyStore(path=None)
    handler, recorder, executions = build(store)

    assert handler.handle(envelope(action_id="act-1")) == "executed"
    assert handler.handle(envelope(action_id="act-2")) == "duplicate"

    assert len(executions) == 1
    assert recorder.last["result"]["first_action_id"] == "act-1"
    assert recorder.last["result"]["first_status"] == "success", (
        "the duplicate reported the claim status instead of the run's outcome"
    )


def test_failed_run_is_recorded_as_failure():
    store = IdempotencyStore(path=None)
    recorder = Recorder()

    def failing(max_duration_seconds: float, stop_requested=None) -> dict:
        return {"success": False, "status": "failure"}

    handler = AtlasActionHandler(
        recorder, execute=failing, synchronous=True, idempotency=store
    )
    handler.handle(envelope(action_id="act-1"))
    handler.handle(envelope(action_id="act-2"))
    assert recorder.last["result"]["first_status"] == "failure"


def test_outcome_survives_a_restart(tmp_path):
    path = tmp_path / "idempotency.jsonl"
    first, _, _ = build(IdempotencyStore(path=path))
    first.handle(envelope(action_id="act-1"))

    second, recorder, runs = build(IdempotencyStore(path=path))
    second.handle(envelope(action_id="act-2"))
    assert runs == []
    assert recorder.last["result"]["first_status"] == "success"
