import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from registry.vendors.robopay.robopay_bridge import _extract_simulator_metrics, _normalize_action, _write_state_file


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
