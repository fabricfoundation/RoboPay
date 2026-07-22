import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:  # pragma: no cover - exercised only when Webots is installed
    from controller import Robot as WebotsRobot  # type: ignore
except ImportError:  # pragma: no cover - fallback for local/dev environments
    WebotsRobot = None

STATE_FILE = os.environ.get("ROBOPAY_WEBOTS_STATE_FILE", "webots_state.json")
POLL_INTERVAL_SECONDS = 1.0


def _read_state_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _ensure_state_file(path: Path) -> None:
    try:
        os.makedirs(path.parent, exist_ok=True)
    except OSError:
        return
    if not path.exists():
        try:
            path.write_text("{}", encoding="utf-8")
        except OSError:
            return


def _connect_webots_robot() -> Tuple[Optional[Any], int]:
    if WebotsRobot is None:
        return None, 0
    try:
        robot = WebotsRobot()
        timestep = int(robot.getBasicTimeStep())
        return robot, timestep
    except Exception as exc:  # pragma: no cover - runtime-only guard
        print(f"[Webots Controller] unable to connect to Webots robot: {exc}")
        return None, 0


def _react_to_state(state: Optional[Dict[str, Any]], robot: Optional[Any] = None) -> Dict[str, Any]:
    command = None
    execution_state = "idle"
    if isinstance(state, dict):
        command = state.get("command")
        execution_state = state.get("execution_state", "idle")

    if command in {"walk", "move_forward", "forward"}:
        behavior = "walking"
    elif command == "stand":
        behavior = "standing"
    elif command == "sit":
        behavior = "sitting"
    elif command in {"stop", "idle"}:
        behavior = "stopped"
    else:
        behavior = "ready"

    if robot is not None:
        try:
            robot.step(1)
        except Exception:  # pragma: no cover - runtime-only guard
            pass

    return {
        "command": command,
        "execution_state": execution_state,
        "behavior": behavior,
    }


def run_controller(state_file: Optional[str] = None, poll_interval_seconds: float = POLL_INTERVAL_SECONDS) -> None:
    path = Path(state_file or STATE_FILE)
    _ensure_state_file(path)
    print(f"[Webots Controller] Watching '{path}'")

    robot, timestep = _connect_webots_robot()
    if robot is not None:
        print(f"[Webots Controller] connected to Webots step loop with timestep={timestep}")
    else:
        print("[Webots Controller] Webots runtime not available; falling back to polling mode")

    last_command: Optional[str] = None
    while True:
        _ensure_state_file(path)
        state = _read_state_file(path)
        if state:
            current_command = state.get("command")
            if current_command != last_command:
                reaction = _react_to_state(state, robot=robot)
                print(
                    f"[Webots Controller] command={reaction['command']} "
                    f"execution_state={reaction['execution_state']} behavior={reaction['behavior']}"
                )
                last_command = current_command
        else:
            print("[Webots Controller] waiting for bridge state...")
        time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    try:
        run_controller()
    except KeyboardInterrupt:
        print("\n[Webots Controller] stopped")
        sys.exit(0)
