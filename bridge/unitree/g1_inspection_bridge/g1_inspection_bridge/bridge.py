"""Fail-closed Zenoh execution bridge for the paid Unitree G1 skill."""

from __future__ import annotations

import importlib.util
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .control_core import VALID_TARGETS
from .runner import run_inspection


LOGGER = logging.getLogger("robopay.unitree_g1")
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/unitree_g1/metrics"
ROBOT_ID = "unitree-g1-sim-01"
PROFILE_ID = "unitree.g1.mujoco-webots-active-inspection.v1"
ALLOWED_ACTIONS = {"inspect_target_sequence", "stop"}
INSPECTION_PARAMS = {"maxDurationSec", "targets", "speedScale"}


class ActionContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _inspection_params(params: dict) -> tuple[float, tuple[str, ...], float]:
    if not isinstance(params, dict):
        raise ActionContractError("INVALID_PARAMS", "params must be an object")
    unexpected = sorted(set(params) - INSPECTION_PARAMS)
    if unexpected:
        raise ActionContractError("INVALID_PARAMS", f"unregistered parameter(s): {', '.join(unexpected)}")
    duration = params.get("maxDurationSec", 18.0)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(float(duration)) or not 5 <= float(duration) <= 30:
        raise ActionContractError("INVALID_DURATION", "maxDurationSec must be between 5 and 30")
    raw_targets = params.get("targets", list(VALID_TARGETS))
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 3 or any(not isinstance(item, str) or item not in VALID_TARGETS for item in raw_targets) or len(set(raw_targets)) != len(raw_targets):
        raise ActionContractError("INVALID_TARGETS", "targets must be one to three unique left/center/right values")
    speed = params.get("speedScale", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(float(speed)) or not 0.5 <= float(speed) <= 1.0:
        raise ActionContractError("INVALID_SPEED", "speedScale must be between 0.5 and 1.0")
    return float(duration), tuple(raw_targets), float(speed)


def _visual_payment_demo() -> tuple[bool, float | None, float, float]:
    enabled = os.environ.get("UNITREE_G1_MUJOCO_VIEWER", "").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return False, None, 0.0, 0.0
    try:
        hold = float(os.environ.get("UNITREE_G1_MUJOCO_VIEWER_HOLD_SECONDS", "5"))
        target_hold = float(os.environ.get("UNITREE_G1_TARGET_HOLD_SECONDS", "0"))
        start_hold = float(os.environ.get("UNITREE_G1_VIEWER_START_HOLD_SECONDS", "0"))
    except ValueError as error:
        raise ActionContractError("INVALID_VIEWER_CONFIG", "viewer holds must be non-negative numbers") from error
    if any(not math.isfinite(value) or value < 0 for value in (hold, target_hold, start_hold)):
        raise ActionContractError("INVALID_VIEWER_CONFIG", "viewer holds must be non-negative numbers")
    return True, hold, target_hold, start_hold


@dataclass(frozen=True)
class BridgeSettings:
    robot_id: str
    zenoh_endpoint: str | None
    zenoh_config_path: str | None
    action_topic: str
    result_topic: str
    metrics_topic: str

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        value = lambda name, default: os.environ.get(name, default).strip() or default
        return cls(value("ROBOT_ID", ROBOT_ID), os.environ.get("ZENOH_ENDPOINT", "").strip() or None, os.environ.get("ZENOH_CONFIG", "").strip() or None, value("ZENOH_ACTION_TOPIC", ACTION_TOPIC), value("ZENOH_RESULT_TOPIC", RESULT_TOPIC), value("ZENOH_METRICS_TOPIC", METRICS_TOPIC))


def _load_event_parser():
    path = Path(__file__).resolve().parents[3] / "common" / "zenoh_bridge" / "zenoh_bridge" / "action_event.py"
    spec = importlib.util.spec_from_file_location("robopay_action_event", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load ActionEvent parser: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_action_event


def _open_session(settings: BridgeSettings):
    import zenoh
    if settings.zenoh_config_path:
        return zenoh.open(zenoh.Config.from_file(settings.zenoh_config_path))
    if settings.zenoh_endpoint:
        return zenoh.open(zenoh.Config.from_json5(json.dumps({"mode": "client", "connect": {"endpoints": [settings.zenoh_endpoint]}})))
    return zenoh.open(zenoh.Config())


class G1ZenohBridge:
    def __init__(self, model_dir: str | None = None, settings: BridgeSettings | None = None):
        self.settings = settings or BridgeSettings.from_env()
        self.robot_id = self.settings.robot_id
        self._model_dir = model_dir
        self._parse = _load_event_parser()
        self._session = _open_session(self.settings)
        self._results = self._session.declare_publisher(self.settings.result_topic)
        self._metrics = self._session.declare_publisher(self.settings.metrics_topic)
        self._stop = threading.Event()
        self._stop_applied = threading.Event()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._subscriber = self._session.declare_subscriber(self.settings.action_topic, self._on_action)
        ready_path = os.environ.get("UNITREE_G1_READY_FILE", "").strip()
        if ready_path:
            Path(ready_path).write_text(
                json.dumps(
                    {
                        "robot_id": self.robot_id,
                        "action_topic": self.settings.action_topic,
                        "result_topic": self.settings.result_topic,
                        "ready": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

    def _publish(self, event, status: str, result: dict) -> None:
        envelope = {"action_id": event.action_id, "robot_id": event.robot_id, "skill_id": event.skill_id, "params_hash": event.params_hash, "idempotency_key": event.idempotency_key, "status": status, "profile_id": PROFILE_ID, "result": result}
        payload = json.dumps(envelope).encode()
        self._metrics.put(payload); self._results.put(payload)

    def _execute(self, event, duration: float, targets: tuple[str, ...], speed: float, viewer: bool, hold: float | None, target_hold: float, start_hold: float) -> None:
        try:
            result = run_inspection(
                self._model_dir, duration, targets, speed,
                viewer=viewer, viewer_hold_seconds=hold,
                viewer_target_hold_seconds=target_hold,
                viewer_start_hold_seconds=start_hold, stop_requested=self._stop.is_set,
            )
        except Exception as error:
            LOGGER.exception("G1 simulator execution failed")
            result = {"success": False, "error_code": "SIMULATOR_EXECUTION_ERROR", "message": str(error)}
        if result.get("safe_stop_applied"):
            self._stop_applied.set()
        LOGGER.info(
            "G1 action completed action_id=%s status=%s targets_confirmed=%s",
            event.action_id,
            "success" if result.get("success") else "failure",
            result.get("targets_confirmed", []),
        )
        self._publish(event, "success" if result.get("success") else "failure", result)

    def _on_action(self, sample) -> None:
        event = self._parse(bytes(sample.payload.to_bytes()))
        if event is None or event.robot_id != self.robot_id:
            return
        if event.action != event.skill_id:
            self._publish(event, "failure", {"success": False, "error_code": "ACTION_SKILL_MISMATCH"}); return
        action = event.action.lower()
        if action not in ALLOWED_ACTIONS:
            self._publish(event, "failure", {"success": False, "error_code": "UNREGISTERED_ACTION"}); return
        if action == "stop":
            if event.params:
                self._publish(event, "failure", {"success": False, "error_code": "INVALID_PARAMS"}); return
            self._stop.set()
            with self._lock:
                interrupted = self._worker is not None and self._worker.is_alive()
            confirmed = not interrupted or self._stop_applied.wait(5.0)
            self._publish(event, "success" if confirmed else "failure", {"success": confirmed, "safe_stop_applied": confirmed, "active_execution_interrupted": interrupted, "error_code": None if confirmed else "SAFE_STOP_TIMEOUT"})
            return
        try:
            duration, targets, speed = _inspection_params(event.params)
            viewer, hold, target_hold, start_hold = _visual_payment_demo()
        except ActionContractError as error:
            self._publish(event, "failure", {"success": False, "error_code": error.code, "message": str(error)}); return
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                self._publish(event, "failure", {"success": False, "error_code": "ROBOT_BUSY"}); return
            self._stop.clear(); self._stop_applied.clear()
            LOGGER.info(
                "Authorized G1 ActionEvent accepted action_id=%s robot_id=%s targets=%s",
                event.action_id,
                event.robot_id,
                targets,
            )
            self._worker = threading.Thread(target=self._execute, args=(event, duration, targets, speed, viewer, hold, target_hold, start_hold), daemon=True, name=f"g1-action-{event.action_id}")
            self._worker.start()

    def spin(self) -> None:
        LOGGER.info(
            "G1 bridge robot_id=%s listening=%s results=%s metrics=%s",
            self.robot_id,
            self.settings.action_topic,
            self.settings.result_topic,
            self.settings.metrics_topic,
        )
        try:
            while True:
                time.sleep(0.1)
        finally:
            self._stop.set()
            self._subscriber.undeclare(); self._results.undeclare(); self._metrics.undeclare(); self._session.close()
            ready_path = os.environ.get("UNITREE_G1_READY_FILE", "").strip()
            if ready_path:
                Path(ready_path).unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    G1ZenohBridge().spin()


if __name__ == "__main__":
    main()
