from __future__ import annotations

from pathlib import Path

from limx_tron2_sim.bridge import DurableReplayStore, LimXTron2Execution
from limx_tron2_sim.runtime import run_mujoco_episode

from conftest import correlated_event


def _runner_must_not_run(_request):
    raise AssertionError("an invalid or untrusted event reached the simulator")


def test_missing_payment_evidence_and_unknown_action_never_reach_simulator(tmp_path: Path) -> None:
    execution = LimXTron2Execution(
        replay_store=DurableReplayStore(tmp_path / "replay.sqlite3"), episode_runner=_runner_must_not_run
    )
    assert execution.process(correlated_event(include_payment=False)) is None
    rejected = execution.process(correlated_event(action="unknown_skill", skill_id="unknown_skill"))
    assert rejected is not None
    assert rejected["status"] == "failure"
    assert rejected["result"]["error_code"] == "ACTION_CONTRACT_REJECTED"
    assert execution.replay_store.action_count() == 0


def test_real_mujoco_action_is_durable_and_cannot_replay_after_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "replay.sqlite3"
    event = correlated_event(action_id="real-001", idempotency_key="idem-real-001", payment_nonce="payment-real-001")
    first_boundary = LimXTron2Execution(replay_store=DurableReplayStore(state_path), episode_runner=run_mujoco_episode)
    first = first_boundary.process(event)
    assert first is not None
    assert first["status"] == "success"
    assert first["result"]["simulator"] == "mujoco"
    assert len(first["result"]["detected_obstacles"]) == 3

    # This is a fresh execution object and a fresh SQLite connection, not an
    # in-memory replay cache. The runner is forbidden from running again.
    restarted_boundary = LimXTron2Execution(
        replay_store=DurableReplayStore(state_path), episode_runner=_runner_must_not_run
    )
    replay = restarted_boundary.process(event)
    assert replay is not None
    assert replay["status"] == "failure"
    assert replay["result"]["error_code"] == "REPLAY_DETECTED"
    assert restarted_boundary.replay_store.action_count() == 1


def test_payment_fingerprint_blocks_same_payment_with_a_fresh_idempotency_key(tmp_path: Path) -> None:
    store = DurableReplayStore(tmp_path / "replay.sqlite3")
    first = LimXTron2Execution(replay_store=store, episode_runner=run_mujoco_episode)
    assert first.process(correlated_event(action_id="payment-001", idempotency_key="key-001", payment_nonce="same-payment"))["status"] == "success"
    second = LimXTron2Execution(replay_store=store, episode_runner=_runner_must_not_run)
    replay = second.process(correlated_event(action_id="payment-002", idempotency_key="key-002", payment_nonce="same-payment"))
    assert replay is not None
    assert replay["result"]["error_code"] == "PAYMENT_REPLAY_DETECTED"
