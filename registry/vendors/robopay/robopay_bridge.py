import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple
from urllib.parse import urlparse

try:
    import zenoh
except ImportError:  # pragma: no cover - exercised when zenoh is unavailable
    zenoh = None

PROCESSED_ACTIONS: Set[str] = set()
SIMULATOR_STATE: Dict[str, Any] = {
    "execution_state": "idle",
    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
    "target_pose": {"x": 0.0, "y": 0.0, "z": 0.0},
    "command": None,
    "last_error": None,
}


def _decode_payload(sample: Any) -> str:
    payload = getattr(sample, "payload", None)
    if payload is None:
        return ""
    if hasattr(payload, "to_string"):
        return payload.to_string()
    if isinstance(payload, (bytes, bytearray)):
        return payload.decode("utf-8")
    return str(payload)


def _build_result(
    action_id: str,
    status: str,
    execution_time_ms: int,
    simulator_metrics: Optional[Dict[str, Any]] = None,
    settled: bool = True,
) -> Dict[str, Any]:
    return {
        "actionId": action_id,
        "status": status,
        "execution_time_ms": execution_time_ms,
        "simulator_metrics": simulator_metrics or {},
        "settled": settled,
    }


def _normalize_action(request: Any) -> str:
    if isinstance(request, str):
        action = request.strip().lower()
        return {
            "stand": "stand",
            "walk": "walk",
            "move_forward": "walk",
            "forward": "walk",
            "move_backward": "walk",
            "backward": "walk",
            "sit": "sit",
            "stop": "stop",
        }.get(action, action)

    if isinstance(request, dict):
        for key in ("action", "action_name", "command", "skill_id", "actionId", "action_id"):
            value = request.get(key)
            if isinstance(value, str) and value.strip():
                action = value.strip().lower()
                return {
                    "stand": "stand",
                    "walk": "walk",
                    "move_forward": "walk",
                    "forward": "walk",
                    "move_backward": "walk",
                    "backward": "walk",
                    "sit": "sit",
                    "stop": "stop",
                }.get(action, action)
    return "stand"


def _extract_simulator_metrics(controller_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = dict(SIMULATOR_STATE)
    if isinstance(controller_state, dict):
        state.update(controller_state)

    position = state.get("position")
    if not isinstance(position, dict):
        position = {"x": 0.0, "y": 0.0, "z": 0.0}
    target_pose = state.get("target_pose")
    if not isinstance(target_pose, dict):
        target_pose = dict(position)

    metrics = {
        "execution_state": state.get("execution_state", "idle"),
        "position": {
            "x": float(position.get("x", 0.0)),
            "y": float(position.get("y", 0.0)),
            "z": float(position.get("z", 0.0)),
        },
        "target_pose": {
            "x": float(target_pose.get("x", position.get("x", 0.0))),
            "y": float(target_pose.get("y", position.get("y", 0.0))),
            "z": float(target_pose.get("z", position.get("z", 0.0))),
        },
        "command": state.get("command"),
        "last_error": state.get("last_error"),
    }
    if "transport" in state:
        metrics["transport"] = state["transport"]
    return metrics


def _read_state_file(state_file: Optional[str]) -> Optional[Dict[str, Any]]:
    if not state_file:
        return None
    path = Path(state_file)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        return None
    return None


def _write_state_file(state_file: Optional[str], state: Dict[str, Any]) -> None:
    if not state_file:
        return
    path = Path(state_file)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    try:
        os.makedirs(path.parent, exist_ok=True)
    except OSError:
        temp_root = Path(os.environ.get("TEMP", Path.cwd().as_posix()))
        temp_root.mkdir(parents=True, exist_ok=True)
        path = (temp_root / path.name).resolve()
        os.makedirs(path.parent, exist_ok=True)
    try:
        os.makedirs(path.parent, exist_ok=True)
        if not path.exists():
            path.write_text("{}", encoding="utf-8")
        with path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
    except OSError as exc:
        print(f"[Webots] Unable to write simulator state file '{state_file}': {exc}")


def _run_one_shot(request: Dict[str, Any]) -> Dict[str, Any]:
    command = _normalize_action(request)
    sent, simulator_metrics = _send_webots_command(command, request)
    return _build_result(
        request.get("actionId") or request.get("action_id") or "unknown",
        "completed" if sent else "failed",
        0,
        {
            "skill_id": request.get("skill_id") or request.get("skillId") or "",
            "payment_verified": True,
            "simulator": "robopay-tier1",
            "execution_mode": "simulated",
            "action": command,
            "execution_state": simulator_metrics.get("execution_state", "idle"),
            "position": simulator_metrics.get("position", {}),
            "target_pose": simulator_metrics.get("target_pose", {}),
            "command": simulator_metrics.get("command"),
            "last_error": simulator_metrics.get("last_error"),
            "transport": simulator_metrics.get("transport"),
        },
        settled=True,
    )


def _send_webots_command(command: str, request: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    state = dict(SIMULATOR_STATE)
    state["command"] = command

    state_file = os.environ.get("ROBOPAY_WEBOTS_STATE_FILE")
    if state_file:
        persisted = _read_state_file(state_file)
        if isinstance(persisted, dict):
            state.update(persisted)

    position = state.get("position")
    if not isinstance(position, dict):
        position = {"x": 0.0, "y": 0.0, "z": 0.0}
    target_pose = state.get("target_pose")
    if not isinstance(target_pose, dict):
        target_pose = dict(position)

    if command == "stand":
        state["execution_state"] = "standing"
        target_pose = dict(position)
    elif command in {"walk", "move_forward", "forward", "move_backward", "backward"}:
        state["execution_state"] = "walking"
        offset = 0.1 if command in {"walk", "move_forward", "forward"} else -0.1
        target_pose["x"] = round(position.get("x", 0.0) + offset, 3)
        target_pose["y"] = round(position.get("y", 0.0), 3)
        target_pose["z"] = round(position.get("z", 0.0), 3)
        position["x"] = target_pose["x"]
    elif command == "sit":
        state["execution_state"] = "sitting"
    else:
        state["execution_state"] = "ready"

    state["position"] = position
    state["target_pose"] = target_pose
    state["last_error"] = None

    SIMULATOR_STATE.clear()
    SIMULATOR_STATE.update(state)

    payload = {
        "command": command,
        "request": request,
        "state": _extract_simulator_metrics(state),
    }

    if state_file:
        _write_state_file(state_file, payload["state"])
        state["transport"] = "state-file"
        SIMULATOR_STATE.clear()
        SIMULATOR_STATE.update(state)
        return True, _extract_simulator_metrics(state)

    command_url = os.environ.get("ROBOPAY_WEBOTS_COMMAND_URL")
    if command_url:
        parsed = urlparse(command_url)
        if parsed.scheme == "tcp" and parsed.hostname and parsed.port is not None:
            try:
                with socket.create_connection((parsed.hostname, parsed.port), timeout=2) as sock:
                    sock.sendall(json.dumps(payload).encode("utf-8"))
                    sock.shutdown(socket.SHUT_WR)
                    response = sock.recv(4096).decode("utf-8")
                if response:
                    try:
                        response_state = json.loads(response)
                    except json.JSONDecodeError:
                        response_state = None
                    if isinstance(response_state, dict):
                        state.update(response_state)
                        SIMULATOR_STATE.clear()
                        SIMULATOR_STATE.update(state)
                        state["transport"] = "tcp"
                        return True, _extract_simulator_metrics(state)
            except OSError as exc:
                state["transport"] = "tcp"
                state["last_error"] = str(exc)
                SIMULATOR_STATE.clear()
                SIMULATOR_STATE.update(state)
                return False, _extract_simulator_metrics(state)

        state["transport"] = "configured"
        SIMULATOR_STATE.clear()
        SIMULATOR_STATE.update(state)
        return True, _extract_simulator_metrics(state)

    state["transport"] = "local-fallback"
    SIMULATOR_STATE.clear()
    SIMULATOR_STATE.update(state)
    return True, _extract_simulator_metrics(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="RoboPay Webots bridge")
    parser.add_argument("--action", dest="action", help="Run a one-shot action locally")
    parser.add_argument("--action-id", dest="action_id", default="one-shot")
    parser.add_argument("--skill-id", dest="skill_id", default="")
    parser.add_argument("--payment-proof", dest="payment_proof", default="")
    args = parser.parse_args()

    if args.action:
        request = {
            "actionId": args.action_id,
            "action": args.action,
            "skill_id": args.skill_id,
            "payment_proof": args.payment_proof or "local-demo",
        }
        response = _run_one_shot(request)
        print(json.dumps(response))
        return

    if zenoh is None:
        print("[ERROR] Zenoh module not found. Run 'python -m pip install eclipse-zenoh'")
        return

    print("==========================================")
    print("  RoboPay Zenoh Action Bridge v1.1  ")
    print("==========================================")

    conf = zenoh.Config()
    print("[Zenoh] Opening session...")
    session = zenoh.open(conf)

    request_topic = "robopay/action/request"
    result_topic = "robopay/action/result"

    publisher = session.declare_publisher(result_topic)
    print(f"[Zenoh] Subscribing to '{request_topic}' and publishing results to '{result_topic}'")

    def handle_request(sample: Any) -> None:
        start_time = time.perf_counter()
        try:
            raw_payload = _decode_payload(sample)
            request = json.loads(raw_payload) if raw_payload else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = _build_result(
                "unknown",
                "invalid_payload",
                0,
                {"reason": "request payload was not valid JSON"},
                settled=False,
            )
            publisher.put(json.dumps(response).encode("utf-8"))
            print(f"[TX] {json.dumps(response)}")
            return

        action_id = request.get("actionId") or request.get("action_id") or "unknown"
        skill_id = request.get("skill_id") or request.get("skillId") or ""
        payment_proof = request.get("payment_proof")

        if action_id in PROCESSED_ACTIONS:
            print(f"[Zenoh] Dropping replayed action request: {action_id}")
            return

        if not payment_proof:
            response = _build_result(
                action_id,
                "rejected",
                0,
                {
                    "skill_id": skill_id,
                    "payment_verified": False,
                    "reason": "payment_proof missing",
                },
                settled=False,
            )
            publisher.put(json.dumps(response).encode("utf-8"))
            print(f"[TX] {json.dumps(response)}")
            return

        PROCESSED_ACTIONS.add(action_id)
        command = _normalize_action(request)
        sent, simulator_metrics = _send_webots_command(command, request)
        execution_time_ms = int((time.perf_counter() - start_time) * 1000)
        response = _build_result(
            action_id,
            "completed" if sent else "failed",
            execution_time_ms,
            {
                "skill_id": skill_id,
                "payment_verified": True,
                "simulator": "robopay-tier1",
                "execution_mode": "simulated",
                "action": command,
                "execution_state": simulator_metrics.get("execution_state", "idle"),
                "position": simulator_metrics.get("position", {}),
                "target_pose": simulator_metrics.get("target_pose", {}),
                "command": simulator_metrics.get("command"),
                "last_error": simulator_metrics.get("last_error"),
                "transport": simulator_metrics.get("transport"),
            },
            settled=True,
        )
        publisher.put(json.dumps(response).encode("utf-8"))
        print(f"[TX] {json.dumps(response)}")

    session.declare_subscriber(request_topic, handle_request)
    print(f"[Zenoh] Listening for action events on '{request_topic}'")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Zenoh] Closing session...")
        session.close()


if __name__ == "__main__":
    main()