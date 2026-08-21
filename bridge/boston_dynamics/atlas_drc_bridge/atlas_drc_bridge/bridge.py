"""Zenoh bridge that executes only Tunnel-correlated Atlas actions."""

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

from .contracts import (
    ATLAS_ROBOT_ID,
    PROFILE_ID,
    STOP_SKILL_ID,
    ActionContractError,
    validate_action,
)
from .runtime import run_wave_episode


LOGGER = logging.getLogger("robopay.atlas_drc")
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/boston_dynamics_atlas_drc/metrics"
READY_TOPIC = "robot/boston_dynamics_atlas_drc/ready"


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
        def configured(name: str, default: str) -> str:
            return os.environ.get(name, default).strip() or default

        return cls(
            robot_id=configured("ROBOT_ID", ATLAS_ROBOT_ID),
            zenoh_endpoint=os.environ.get("ZENOH_ENDPOINT", "").strip() or None,
            zenoh_config_path=os.environ.get("ZENOH_CONFIG", "").strip() or None,
            action_topic=configured("ZENOH_ACTION_TOPIC", ACTION_TOPIC),
            result_topic=configured("ZENOH_RESULT_TOPIC", RESULT_TOPIC),
            metrics_topic=configured("ZENOH_METRICS_TOPIC", METRICS_TOPIC),
        )


def _open_zenoh_session(settings: BridgeSettings):
    import zenoh

    if settings.zenoh_config_path:
        return zenoh.open(zenoh.Config.from_file(settings.zenoh_config_path))
    if settings.zenoh_endpoint:
        return zenoh.open(
            zenoh.Config.from_json5(
                json.dumps(
                    {
                        "mode": "client",
                        "connect": {"endpoints": [settings.zenoh_endpoint]},
                    }
                )
            )
        )
    raise RuntimeError(
        "Refusing an implicit Zenoh session. Configure ZENOH_CONFIG for the "
        "private Tunnel-to-bridge boundary (or ZENOH_ENDPOINT for a controlled local test)."
    )


def _load_event_parser():
    parser_path = (
        Path(__file__).resolve().parents[3]
        / "common"
        / "zenoh_bridge"
        / "zenoh_bridge"
        / "action_event.py"
    )
    spec = importlib.util.spec_from_file_location("robopay_atlas_action_event", parser_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load shared ActionEvent parser from {parser_path}")
    module = importlib.util.module_from_spec(spec)
    # ``action_event.py`` defines dataclasses.  Registering its module before
    # execution makes its annotations resolvable in the same way as a normal
    # import (and avoids a test-only import-path difference).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.parse_action_event


class AtlasZenohBridge:
    """A second authorization boundary before physics can receive an action."""

    def __init__(
        self,
        model_dir: str | None = None,
        settings: BridgeSettings | None = None,
        episode_runner: Callable[..., dict] = run_wave_episode,
    ):
        self.settings = settings or BridgeSettings.from_env()
        self.robot_id = self.settings.robot_id
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
        self._session.put(
            READY_TOPIC,
            json.dumps(
                {
                    "status": "ready",
                    "robot_id": self.robot_id,
                    "action_topic": self.settings.action_topic,
                },
                separators=(",", ":"),
            ).encode("utf-8"),
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

    def _execute_wave(self, event, params) -> None:
        try:
            runner_options = {}
            if os.environ.get("ATLAS_MUJOCO_VIEWER", "").strip().lower() in {"1", "true", "yes"}:
                try:
                    hold_seconds = max(0.0, float(os.environ.get("ATLAS_MUJOCO_VIEWER_HOLD_SECONDS", "10")))
                except ValueError:
                    hold_seconds = 10.0
                try:
                    start_hold_seconds = max(
                        0.0,
                        float(os.environ.get("ATLAS_MUJOCO_VIEWER_START_HOLD_SECONDS", "3")),
                    )
                    turn_hold_seconds = max(
                        0.0,
                        float(os.environ.get("ATLAS_MUJOCO_VIEWER_TURN_HOLD_SECONDS", "0.45")),
                    )
                except ValueError:
                    start_hold_seconds = 3.0
                    turn_hold_seconds = 0.45
                runner_options = {
                    "viewer": True,
                    "viewer_hold_seconds": hold_seconds,
                    "viewer_start_hold_seconds": start_hold_seconds,
                    "viewer_turn_hold_seconds": turn_hold_seconds,
                }
            result = self._episode_runner(
                params,
                model_dir=self._model_dir,
                stop_requested=self._stop_event.is_set,
                **runner_options,
            )
        except Exception as error:
            LOGGER.exception("Atlas simulator execution failed")
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
        if event.robot_id != self.robot_id:
            return
        if event.action != event.skill_id:
            self._publish(event, "failure", {"success": False, "error_code": "ACTION_SKILL_MISMATCH"})
            return
        try:
            params = validate_action(event.action, event.params)
        except ActionContractError as error:
            self._publish(
                event,
                "failure",
                {"success": False, "error_code": error.code, "message": str(error)},
            )
            return

        if event.action == STOP_SKILL_ID:
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
                target=self._execute_wave,
                args=(event, params),
                daemon=True,
                name=f"atlas-wave-{event.action_id}",
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
        self._session.close()

    def spin(self) -> None:  # pragma: no cover - process entry point
        LOGGER.info(
            "Atlas bridge %s subscribes %s and publishes %s",
            self.robot_id,
            self.settings.action_topic,
            self.settings.result_topic,
        )
        try:
            while True:
                time.sleep(0.1)
        finally:
            self.close()


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO)
    AtlasZenohBridge().spin()


if __name__ == "__main__":
    main()
