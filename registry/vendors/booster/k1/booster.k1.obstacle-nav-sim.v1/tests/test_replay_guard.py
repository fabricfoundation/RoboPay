"""
Unit tests for bridge/replay_guard.py.

Core requirement (explicitly called out in review): a replayed action
-- whether identified by the same idempotencyKey, the same actionId,
or the same payment.authorizationId -- must NEVER cause a second
execution or a second settlement attempt. These tests simulate the
exact "send the same paid action twice" scenario a malicious or buggy
client could attempt.
"""
import os
import sys
import tempfile

import pytest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(THIS_DIR, "..", "bridge"))

from replay_guard import ReplayGuard, ReplayDetected, Fingerprint  # noqa: E402


@pytest.fixture
def guard():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    g = ReplayGuard(path)
    yield g
    g.close()
    os.remove(path)


def make_fp(action_id="act_1", robot_id="booster-k1-sim-01",
            skill_id="k1_navigate_avoid_obstacles",
            params_hash="sha256:abc", auth_id="auth_1"):
    return Fingerprint(action_id, robot_id, skill_id, params_hash, auth_id)


def test_first_execution_is_allowed(guard):
    """A brand-new action must be reservable without error."""
    guard.check_and_reserve("idem_1", make_fp())  # must not raise


def test_exact_replay_same_idempotency_key_rejected(guard):
    """Sending the identical paid action twice (e.g. client retry after
    a network timeout) must be rejected on the second attempt -- it
    must NOT trigger a second execution in the simulator."""
    fp = make_fp()
    guard.check_and_reserve("idem_1", fp)
    with pytest.raises(ReplayDetected) as exc:
        guard.check_and_reserve("idem_1", fp)
    assert "idempotencyKey" in exc.value.reason


def test_replay_with_different_idempotency_key_same_action_id_rejected(guard):
    """A client trying to bypass idempotencyKey dedup by generating a
    fresh key but reusing the same actionId must still be rejected."""
    guard.check_and_reserve("idem_1", make_fp(action_id="act_1"))
    with pytest.raises(ReplayDetected) as exc:
        guard.check_and_reserve("idem_2", make_fp(action_id="act_1"))
    assert "actionId" in exc.value.reason


def test_replay_with_same_authorization_id_rejected(guard):
    """Reusing a payment authorizationId across two different actionIds
    must be rejected -- this is the core anti-double-spend guarantee:
    one payment authorization can fund at most one execution."""
    guard.check_and_reserve("idem_1", make_fp(action_id="act_1", auth_id="auth_1"))
    with pytest.raises(ReplayDetected) as exc:
        guard.check_and_reserve("idem_2", make_fp(action_id="act_2", auth_id="auth_1"))
    assert "authorizationId" in exc.value.reason


def test_different_actions_are_independent(guard):
    """Sanity check: legitimately different actions (different key,
    actionId, and authorizationId) must NOT be rejected as replays."""
    guard.check_and_reserve("idem_1", make_fp(action_id="act_1", auth_id="auth_1"))
    guard.check_and_reserve("idem_2", make_fp(action_id="act_2", auth_id="auth_2"))  # must not raise


def test_result_can_be_recorded_after_reservation(guard):
    fp = make_fp()
    guard.check_and_reserve("idem_1", fp)
    guard.record_result("idem_1", "success")  # must not raise

    cur = guard._conn.execute(
        "SELECT result_status FROM executed_actions WHERE idempotency_key = ?", ("idem_1",)
    )
    assert cur.fetchone()[0] == "success"


def test_concurrent_duplicate_reservation_only_one_wins(guard):
    """Simulates two near-simultaneous requests for the same action:
    the guard reserves the slot on first check_and_reserve, so the
    second call -- even if 'concurrent' in a real system -- sees the
    row already present and is rejected."""
    fp = make_fp()
    guard.check_and_reserve("idem_1", fp)
    with pytest.raises(ReplayDetected):
        guard.check_and_reserve("idem_1", fp)
