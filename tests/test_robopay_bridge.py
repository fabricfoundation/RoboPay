import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry.vendors.robopay.robopay_bridge import (
    _build_result,
    _decode_payload,
    _extract_simulator_metrics,
    _normalize_action,
    _write_state_file,
)


def test_normalize_action_maps_common_actions() -> None:
    assert _normalize_action("stand") == "stand"
    assert _normalize_action("walk") == "walk"
    assert _normalize_action("move_forward") == "walk"
    assert _normalize_action("sit") == "sit"


def test_extract_simulator_metrics_uses_controller_state() -> None:
    controller_state = {
        "execution_state": "running",
        "position": {"x": 0.25, "y": 0.0, "z": 0.5},
        "target_pose": {"x": 1.0, "y": 0.0, "z": 0.5},
        "command": "walk",
    }

    metrics = _extract_simulator_metrics(controller_state)

    assert metrics["execution_state"] == "running"
    assert metrics["position"]["x"] == 0.25
    assert metrics["target_pose"]["x"] == 1.0
    assert metrics["command"] == "walk"


def test_write_state_file_creates_missing_parent_folder_and_file(tmp_path) -> None:
    state_file = tmp_path / "nested" / "webots_state.json"

    _write_state_file(str(state_file), {"execution_state": "standing"})

    assert state_file.exists()
    assert state_file.parent.exists()
    assert "execution_state" in state_file.read_text(encoding="utf-8")


def test_build_result_settled_true_on_success_terminal_state() -> None:
    response = _build_result(
        action_id="test-success",
        status="completed",
        execution_time_ms=100,
        simulator_metrics={"execution_state": "running", "terminal_state": "success"},
        settled=None,
    )

    assert response["settled"] is True
    assert response["simulator_metrics"]["terminal_state"] == "success"


def test_build_result_settled_false_on_failure_terminal_state() -> None:
    response = _build_result(
        action_id="test-failure",
        status="failed",
        execution_time_ms=100,
        simulator_metrics={"execution_state": "running", "terminal_state": "timeout"},
        settled=None,
    )

    assert response["settled"] is False
    assert response["simulator_metrics"]["terminal_state"] == "timeout"


def test_tunnel_verified_payment_required_for_settlement() -> None:
    request = {
        "actionId": "action-123",
        "action": "walk",
        "skill_id": "walk",
        "payment_proof": "proof-ok",
    }
    response = _build_result(
        action_id=request["actionId"],
        status="rejected",
        execution_time_ms=0,
        simulator_metrics={"payment_verified": False},
        settled=False,
    )

    assert response["settled"] is False
    assert response["status"] == "rejected"
