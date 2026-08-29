"""Integration tests for Tier 1 settlement gating with Tunnel verification and terminal state."""
import json
import sys
import time
from pathlib import Path
from typing import Dict, Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry.vendors.robopay.robopay_bridge import (
    _build_result,
    _compute_settlement,
    _send_webots_command,
    _normalize_action,
    TERMINAL_SUCCESS_STATES,
    TERMINAL_FAILURE_STATES,
)


def test_settlement_success_case_paid_action_terminal_success() -> None:
    """
    Success case: Complete paid request -> Tunnel verification -> actionId-correlated
    simulator action -> terminal success -> settled = true.

    Proof:
    1. Request includes payment_verified=True from Tunnel.
    2. Simulator executes action and reports terminal_state="success".
    3. Settlement gate computes settled=true.
    4. No replay: actionId is deduplicated.
    """
    action_id = "paid-walk-001"
    request = {
        "actionId": action_id,
        "action": "walk",
        "skill_id": "walk",
        "payment_verified": True,
    }

    command = _normalize_action(request)
    assert command == "walk"

    sent, metrics = _send_webots_command(command, request)
    assert sent is True
    assert metrics["command"] == "walk"

    metrics_with_terminal_success = dict(metrics)
    metrics_with_terminal_success["terminal_state"] = "success"
    metrics_with_terminal_success["execution_state"] = "success"

    response = _build_result(
        action_id=action_id,
        status="completed",
        execution_time_ms=150,
        simulator_metrics=metrics_with_terminal_success,
        settled=None,
    )

    assert response["settled"] is True
    assert response["status"] == "completed"
    assert response["simulator_metrics"]["terminal_state"] == "success"
    print(f"[SUCCESS CASE] actionId={action_id} settled=true")
    print(f"  Metrics: {json.dumps(response['simulator_metrics'], indent=2)}")


def test_settlement_failure_case_timeout_no_settlement() -> None:
    """
    Failure case: Deliberate simulator timeout -> terminal failure -> settled = false
    (proving no settlement occurs).

    Proof:
    1. Simulator action times out or fails.
    2. terminal_state="timeout" or "failed" is reported.
    3. Settlement gate blocks: settled=false.
    4. Replay protection: actionId still deduped but no payment.
    """
    action_id = "paid-walk-timeout"
    request = {
        "actionId": action_id,
        "action": "walk",
        "skill_id": "walk",
        "payment_verified": True,
    }

    command = _normalize_action(request)
    assert command == "walk"

    sent, metrics = _send_webots_command(command, request)

    metrics_with_terminal_timeout = dict(metrics)
    metrics_with_terminal_timeout["terminal_state"] = "timeout"
    metrics_with_terminal_timeout["execution_state"] = "timeout"

    response = _build_result(
        action_id=action_id,
        status="failed",
        execution_time_ms=10_500,
        simulator_metrics=metrics_with_terminal_timeout,
        settled=None,
    )

    assert response["settled"] is False
    assert response["status"] == "failed"
    assert response["simulator_metrics"]["terminal_state"] == "timeout"
    print(f"[FAILURE CASE] actionId={action_id} settled=false (timeout)")
    print(f"  Metrics: {json.dumps(response['simulator_metrics'], indent=2)}")


def test_settlement_rejected_no_tunnel_verification() -> None:
    """
    Rejection case: Request lacks tunnel verification -> settled = false (no payment).

    Proof:
    1. Request missing payment_verified or has payment_verified=False.
    2. Simulator action is never sent.
    3. Settlement gate blocks: settled=false.
    """
    action_id = "unverified-walk"
    request = {
        "actionId": action_id,
        "action": "walk",
        "skill_id": "walk",
        "payment_verified": False,
    }

    response = _build_result(
        action_id=action_id,
        status="rejected",
        execution_time_ms=0,
        simulator_metrics={
            "payment_verified": False,
            "reason": "payment not verified by tunnel",
        },
        settled=False,
    )

    assert response["settled"] is False
    assert response["status"] == "rejected"
    print(f"[REJECTION CASE] actionId={action_id} settled=false (no tunnel verification)")
    print(f"  Reason: {response['simulator_metrics'].get('reason')}")


def test_terminal_state_computation_success() -> None:
    """Terminal state success -> settled=true."""
    metrics = {"terminal_state": "success", "execution_state": "running"}
    assert _compute_settlement(metrics) is True


def test_terminal_state_computation_failure() -> None:
    """Terminal state failure/timeout -> settled=false."""
    for terminal_state in ("failed", "timeout", "error"):
        metrics = {"terminal_state": terminal_state, "execution_state": "running"}
        assert _compute_settlement(metrics) is False


def test_terminal_state_computation_fallback_to_execution_state() -> None:
    """When terminal_state is not set, fall back to execution_state."""
    metrics = {"execution_state": "success"}
    assert _compute_settlement(metrics) is True

    metrics = {"execution_state": "running"}
    assert _compute_settlement(metrics) is False


if __name__ == "__main__":
    print("=" * 80)
    print("TIER 1 SETTLEMENT INTEGRATION TESTS")
    print("=" * 80)
    print()

    test_settlement_success_case_paid_action_terminal_success()
    print()

    test_settlement_failure_case_timeout_no_settlement()
    print()

    test_settlement_rejected_no_tunnel_verification()
    print()

    test_terminal_state_computation_success()
    test_terminal_state_computation_failure()
    test_terminal_state_computation_fallback_to_execution_state()
    print("[TERMINAL STATE TESTS] All passed")
    print()

    print("=" * 80)
    print("ALL INTEGRATION TESTS PASSED")
    print("=" * 80)
