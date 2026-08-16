"""Zenoh bridge that executes only Tunnel-correlated M20 actions."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .contracts import PROFILE_ID, ROBOT_ID, STOP_SKILL, ContractError, validate_action
from .runtime import run_drive_episode


LOGGER = logging.getLogger("robopay.deep_robotics_m20")
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/deep_robotics_m20/metrics"
READY_TOPIC = "robot/deep_robotics_m20/ready"


@dataclass(frozen=True)
class BridgeSettings:
    robot_id: str
    zenoh_endpoint: str | None
    zenoh_config_path: str | None
    action_topic: str
    result_topic: str
    metrics_topic: str
    ready_topic: str = READY_TOPIC

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        def configured(name: str, default: str) -> str:
            return os.environ.get(name, default).strip() or default

        return cls(
            robot_id=configured("ROBOT_ID", ROBOT_ID),
            zenoh_endpoint=os.environ.get("ZENOH_ENDPOINT", "").strip() or None,
            zenoh_config_path=os.environ.get("ZENOH_CONFIG", "").strip() or None,
            action_topic=configured("ZENOH_ACTION_TOPIC", ACTION_TOPIC),
            result_topic=configured("ZENOH_RESULT_TOPIC", RESULT_TOPIC),
            metrics_topic=configured("ZENOH_METRICS_TOPIC", METRICS_TOPIC),
            ready_topic=configured("ZENOH_READY_TOPIC", READY_TOPIC),
        )


def _open_zenoh_session(settings: BridgeSettings):
    import zenoh

    if settings.zenoh_config_path:
        return zenoh.open(zenoh.Config.from_file(settings.zenoh_config_path))
    if settings.zenoh_endpoint:
        return zenoh.open(
            zenoh.Config.from_json5(
                json.dumps({"mode": "client", "connect": {"endpoints": [settings.zenoh_endpoint]}})
            )
        )
    raise RuntimeError(
        "Refusing an implicit Zenoh session. Configure ZENOH_CONFIG for the private "
        "Tunnel-to-bridge boundary (or ZENOH_ENDPOINT only for a controlled local test)."
    )


def _load_event_parser():
    parser_path = (
        Path(__file__).resolve().parents[3]
        / "common"
        / "zenoh_bridge"
        / "zenoh_bridge"
        / "action_event.py"
    )
    spec = importlib.util.spec_from_file_location("robopay_m20_action_event", parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load shared ActionEvent parser from {parser_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.parse_action_event


class M20ZenohBridge:
    """A fail-closed second authorization boundary before M20 physics runs."""

    def __init__(
        self,
        model_dir: str | None = None,
        settings: BridgeSettings | None = None,
        episode_runner: Callable[..., dict] = run_drive_episode,
    ):
        self.settings = settings or BridgeSettings.from_env()
        self.robot_id = self.settings.robot_id
        if self.robot_id != ROBOT_ID:
            raise RuntimeError(
                f"M20 bridge is profile-scoped to robot_id={ROBOT_ID!r}; got {self.robot_id!r}"
            )
        self._model_dir = model_dir
        self._episode_runner = episode_runner
        self._parse_action_event = _load_event_parser()
        self._session = _open_zenoh_session(self.settings)
        self._result_publisher = self._session.declare_publisher(self.settings.result_topic)
        self._metrics_publisher = self._session.declare_publisher(self.settings.metrics_topic)
        self._stop_event = threading.Event()
        self._stop_confirmed = threading.Event()
        self._worker_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._subscriber = self._session.declare_subscriber(self.settings.action_topic, self._on_action)
        self._ready_publisher = self._session.declare_publisher(self.settings.ready_topic)
        self._ready_publisher.put(
            json.dumps(
                {
                    "status": "ready",
                    "profile_id": PROFILE_ID,
                    "robot_id": ROBOT_ID,
                    "action_topic": self.settings.action_topic,
                    "result_topic": self.settings.result_topic,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def _publish(self, event, status: str, result: dict) -> None:
        envelope = {
            "action_id": event.action_id,
            "robot_id": event.robot_id,
            "skill_id": event.skill_id,
            "params_hash": event.params_hash,
            "idempotency_key": event.idempotency_key,
            "profile_id": PROFILE_ID,
            "status": status,
            "result": result,
        }
        payload = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        self._metrics_publisher.put(payload)
        self._result_publisher.put(payload)

    def _execute_drive(self, event, request) -> None:
        try:
            result = self._episode_runner(
                request,
                model_dir=self._model_dir,
                stop_event=self._stop_event,
                viewer=os.environ.get("M20_MUJOCO_VIEWER", "").strip().lower() in {"1", "true", "yes"},
                viewer_hold_seconds=max(
                    0.0, float(os.environ.get("M20_MUJOCO_VIEWER_HOLD_SECONDS", "5"))
                ),
            )
        except Exception as error:
            LOGGER.exception("M20 simulator execution failed")
            result = {
                "success": False,
                "error_code": "SIMULATOR_EXECUTION_ERROR",
                "message": str(error),
            }
        if result.get("safe_stop_applied"):
            self._stop_confirmed.set()
        self._publish(event, "success" if result.get("success") else "failure", result)

    def _on_action(self, sample) -> None:  # exercised by unit and real-Zenoh integration tests
        event = self._parse_action_event(bytes(sample.payload.to_bytes()))
        if event is None:
            LOGGER.warning("Rejected malformed or uncorrelated ActionEvent before simulation")
            return
        try:
            request = validate_action(event.robot_id, event.action, event.skill_id, event.params)
        except ContractError as error:
            self._publish(
                event,
                "failure",
                {"success": False, "error_code": "ACTION_CONTRACT_REJECTED", "message": str(error)},
            )
            return

        if request.skill_id == STOP_SKILL:
            self._stop_event.set()
            with self._worker_lock:
                active = self._worker is not None and self._worker.is_alive()
            confirmed = not active or self._stop_confirmed.wait(timeout=5.0)
            self._publish(
                event,
                "success" if confirmed else "failure",
                {
                    "success": confirmed,
                    "safe_stop_applied": confirmed,
                    "active_execution_interrupted": active,
                    "error_code": None if confirmed else "SAFE_STOP_TIMEOUT",
                },
            )
            return

        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                self._publish(event, "failure", {"success": False, "error_code": "ROBOT_BUSY"})
                return
            self._stop_event.clear()
            self._stop_confirmed.clear()
            self._worker = threading.Thread(
                target=self._execute_drive,
                args=(event, request),
                daemon=True,
                name=f"m20-drive-{event.action_id}",
            )
            self._worker.start()

    def close(self) -> None:
        self._stop_event.set()
        with self._worker_lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=5)
        self._subscriber.undeclare()
        self._result_publisher.undeclare()
        self._metrics_publisher.undeclare()
        self._ready_publisher.undeclare()
        self._session.close()

    def spin(self) -> None:  # pragma: no cover - process entry point
        LOGGER.info("M20 bridge %s subscribes %s", self.robot_id, self.settings.action_topic)
        try:
            while True:
                time.sleep(0.1)
        finally:
            self.close()


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    M20ZenohBridge().spin()


if __name__ == "__main__":
    main()
