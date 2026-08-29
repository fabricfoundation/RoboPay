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
MAX_ACTION_DURATION_SECONDS = 10.0
TARGET_POSITION_TOLERANCE = 0.05
TERMINAL_SUCCESS_STATES = {"success"}
TERMINAL_FAILURE_STATES = {"failed", "timeout", "error"}


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
    if not isinstance(payload, dict):
        return None
    if "state" in payload and isinstance(payload["state"], dict):
        state = dict(payload["state"])
        if "command" in payload:
            state["command"] = payload["command"]
        return state
    return payload


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


def _find_device(robot: Any, names: Tuple[str, ...]) -> Optional[Any]:
    for name in names:
        try:
            device = robot.getDevice(name)
        except Exception:
            continue
        if device is not None:
            return device
    return None


def _get_robot_position(robot: Any) -> Dict[str, float]:
    if robot is None:
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    gps = _find_device(robot, ("gps", "GPS", "gps_sensor", "position_sensor", "positionSensor"))
    if gps is not None:
        try:
            values = gps.getValues()
            return {
                "x": float(values[0]) if len(values) > 0 else 0.0,
                "y": float(values[1]) if len(values) > 1 else 0.0,
                "z": float(values[2]) if len(values) > 2 else 0.0,
            }
        except Exception:
            pass
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def _set_motor_velocity(robot: Any, left: float, right: float) -> None:
    if robot is None:
        return
    left_motor = _find_device(robot, ("left wheel motor", "left_motor", "leftWheelMotor", "left_wheel_motor"))
    right_motor = _find_device(robot, ("right wheel motor", "right_motor", "rightWheelMotor", "right_wheel_motor"))
    for motor in (left_motor, right_motor):
        if motor is None:
            continue
        try:
            motor.setPosition(float("inf"))
        except Exception:
            pass
    if left_motor is not None:
        try:
            left_motor.setVelocity(float(left))
        except Exception:
            pass
    if right_motor is not None:
        try:
            right_motor.setVelocity(float(right))
        except Exception:
            pass


def _compute_target_pose(command: Optional[str], position: Dict[str, float]) -> Dict[str, float]:
    if command in {"walk", "move_forward", "forward"}:
        return {"x": position["x"] + 0.5, "y": position["y"], "z": position["z"]}
    if command in {"move_backward", "backward"}:
        return {"x": position["x"] - 0.5, "y": position["y"], "z": position["z"]}
    return dict(position)


def _distance(a: Dict[str, float], b: Dict[str, float]) -> float:
    return ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2) ** 0.5


def _terminal_state_for_action(action: Dict[str, Any], position: Dict[str, float]) -> str:
    if action["terminal_state"] in TERMINAL_SUCCESS_STATES | TERMINAL_FAILURE_STATES:
        return action["terminal_state"]
    if action["command"] == "stand" and action["elapsed"] >= 0.5:
        return "success"
    if _distance(position, action["target_pose"]) <= TARGET_POSITION_TOLERANCE:
        return "success"
    if action["elapsed"] >= MAX_ACTION_DURATION_SECONDS:
        return "timeout"
    return "running"


def _react_to_state(state: Optional[Dict[str, Any]], robot: Optional[Any] = None, timestep: int = 1, action_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if action_meta is None:
        action_meta = {
            "command": None,
            "target_pose": {"x": 0.0, "y": 0.0, "z": 0.0},
            "start_time": time.time(),
            "terminal_state": None,
        }

    current_state = state if isinstance(state, dict) else {}
    command = current_state.get("command")

    if command != action_meta["command"]:
        position = _get_robot_position(robot)
        action_meta.update(
            {
                "command": command,
                "target_pose": _compute_target_pose(command, position),
                "start_time": time.time(),
                "terminal_state": None,
            }
        )

    position = _get_robot_position(robot)
    if command in {"walk", "move_forward", "forward"}:
        behavior = "walking"
        _set_motor_velocity(robot, 4.0, 4.0)
    elif command in {"move_backward", "backward"}:
        behavior = "walking"
        _set_motor_velocity(robot, -4.0, -4.0)
    elif command == "sit":
        behavior = "sitting"
        _set_motor_velocity(robot, 0.0, 0.0)
    elif command == "stand":
        behavior = "standing"
        _set_motor_velocity(robot, 0.0, 0.0)
    elif command in {"stop", "idle", None}:
        behavior = "stopped"
        _set_motor_velocity(robot, 0.0, 0.0)
    else:
        behavior = "ready"
        _set_motor_velocity(robot, 0.0, 0.0)

    if robot is not None:
        try:
            robot.step(timestep)
        except Exception:
            pass

    elapsed = time.time() - action_meta["start_time"]
    action_meta["elapsed"] = elapsed
    action_meta["terminal_state"] = _terminal_state_for_action(action_meta, position)

    execution_state = action_meta["terminal_state"] if action_meta["terminal_state"] != "running" else "running"

    return {
        "command": command,
        "execution_state": execution_state,
        "terminal_state": action_meta["terminal_state"],
        "behavior": behavior,
        "position": position,
        "target_pose": action_meta["target_pose"],
        "elapsed_seconds": round(elapsed, 2),
    }


def _write_state_file(path: Path, state: Dict[str, Any]) -> None:
    try:
        os.makedirs(path.parent, exist_ok=True)
    except OSError:
        return
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
    except OSError:
        return


def run_controller(state_file: Optional[str] = None, poll_interval_seconds: float = POLL_INTERVAL_SECONDS) -> None:
    path = Path(state_file or STATE_FILE)
    _ensure_state_file(path)
    print(f"[Webots Controller] Watching '{path}'")

    robot, timestep = _connect_webots_robot()
    if robot is not None:
        print(f"[Webots Controller] connected to Webots step loop with timestep={timestep}")
    else:
        print("[Webots Controller] Webots runtime not available; falling back to polling mode")

    action_meta: Dict[str, Any] = {
        "command": None,
        "target_pose": {"x": 0.0, "y": 0.0, "z": 0.0},
        "start_time": time.time(),
        "terminal_state": None,
        "elapsed": 0.0,
    }
    last_command: Optional[str] = None

    while True:
        _ensure_state_file(path)
        state = _read_state_file(path)
        reaction = _react_to_state(state, robot=robot, timestep=timestep or 1, action_meta=action_meta)
        if reaction["command"] != last_command:
            print(
                f"[Webots Controller] command={reaction['command']} "
                f"execution_state={reaction['execution_state']} behavior={reaction['behavior']}"
            )
            last_command = reaction["command"]

        if reaction["terminal_state"] in TERMINAL_SUCCESS_STATES | TERMINAL_FAILURE_STATES:
            print(
                f"[Webots Controller] terminal_state={reaction['terminal_state']} "
                f"elapsed_seconds={reaction['elapsed_seconds']}"
            )

        _write_state_file(path, reaction)
        time.sleep(poll_interval_seconds)


if __name__ == "__main__":
    try:
        run_controller()
    except KeyboardInterrupt:
        print("\n[Webots Controller] stopped")
        sys.exit(0)


if __name__ == "__main__":
    try:
        run_controller()
    except KeyboardInterrupt:
        print("\n[Webots Controller] stopped")
        sys.exit(0)
