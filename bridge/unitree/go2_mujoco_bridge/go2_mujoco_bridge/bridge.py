"""Zenoh bridge for paid Go2 obstacle-nav actions."""

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

from .runner import run_obstacle_nav


LOGGER = logging.getLogger("robopay.go2")
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/unitree_go2/metrics"
READY_TOPIC = "robot/unitree_go2/ready"
ROBOT_ID = "go2-mujoco-sim-01"
# Keep this exactly aligned with the published registry profile.  The current
# paired Webots/MuJoCo evidence validates only the left reference corridor.
ALLOWED_ACTIONS = {"navigate_obstacles", "stop"}
NAVIGATION_PARAMS = {"maxDurationSec", "side", "speedScale"}
PROFILE_ID = "unitree.go2.mujoco-webots-obstacle-nav.v1"


class ActionContractError(ValueError):
    """A fail-closed bridge contract violation with a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _navigation_params(params: dict) -> tuple[float, str, float]:
    """Validate the profile's bounded duration, route and speed contract."""

    if not isinstance(params, dict):
        raise ActionContractError("INVALID_PARAMS", "params must be an object.")
    unexpected = sorted(set(params) - NAVIGATION_PARAMS)
    if unexpected:
        raise ActionContractError(
            "INVALID_PARAMS",
            f"unregistered parameter(s): {', '.join(unexpected)}",
        )

    raw_duration = params.get("maxDurationSec", 48.0)
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
        raise ActionContractError("INVALID_DURATION", "maxDurationSec must be a number from 5 to 60.")
    max_duration = float(raw_duration)
    if not math.isfinite(max_duration) or not 5.0 <= max_duration <= 60.0:
        raise ActionContractError("INVALID_DURATION", "maxDurationSec must be between 5 and 60.")

    side = params.get("side", "left")
    if side != "left":
        raise ActionContractError("INVALID_SIDE", "Only the validated left corridor is available.")

    raw_speed = params.get("speedScale", 1.0)
    if isinstance(raw_speed, bool) or not isinstance(raw_speed, (int, float)):
        raise ActionContractError("INVALID_SPEED", "speedScale must be a number from 0.5 to 1.0.")
    speed_scale = float(raw_speed)
    if not math.isfinite(speed_scale) or not 0.5 <= speed_scale <= 1.0:
        raise ActionContractError("INVALID_SPEED", "speedScale must be between 0.5 and 1.0.")
    return max_duration, side, speed_scale


@dataclass(frozen=True)
class BridgeSettings:
    """Deployment settings that can be changed without editing source files."""

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

        endpoint = os.environ.get("ZENOH_ENDPOINT", "").strip() or None
        config_path = os.environ.get("ZENOH_CONFIG", "").strip() or None
        return cls(
            robot_id=configured("ROBOT_ID", ROBOT_ID),
            zenoh_endpoint=endpoint,
            zenoh_config_path=config_path,
            action_topic=configured("ZENOH_ACTION_TOPIC", ACTION_TOPIC),
            result_topic=configured("ZENOH_RESULT_TOPIC", RESULT_TOPIC),
            metrics_topic=configured("ZENOH_METRICS_TOPIC", METRICS_TOPIC),
            ready_topic=configured("ZENOH_READY_TOPIC", READY_TOPIC),
        )


def _visual_payment_demo() -> tuple[bool, float | None]:
    """Opt into a bounded native viewer when recording a paid demo."""

    enabled = os.environ.get("GO2_MUJOCO_VIEWER", "").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return False, None
    raw_hold = os.environ.get("GO2_MUJOCO_VIEWER_HOLD_SECONDS", "5")
    try:
        hold_seconds = float(raw_hold)
    except ValueError as error:
        raise RuntimeError("GO2_MUJOCO_VIEWER_HOLD_SECONDS must be a non-negative number.") from error
    if hold_seconds < 0:
        raise RuntimeError("GO2_MUJOCO_VIEWER_HOLD_SECONDS must be a non-negative number.")
    return True, hold_seconds


def _open_zenoh_session(settings: BridgeSettings):
    """Open the configured Zenoh session, including an explicit test router."""

    if settings.zenoh_config_path:
        import zenoh

        return zenoh.open(zenoh.Config.from_file(settings.zenoh_config_path))
    import zenoh

    if settings.zenoh_endpoint:
        config = zenoh.Config.from_json5(
            json.dumps(
                {
                    "mode": "client",
                    "connect": {"endpoints": [settings.zenoh_endpoint]},
                }
            )
        )
        return zenoh.open(config)
    return zenoh.open(zenoh.Config())


def _load_event_parser():
    action_event_path = Path(__file__).resolve().parents[3] / "common" / "zenoh_bridge" / "zenoh_bridge" / "action_event.py"
    spec = importlib.util.spec_from_file_location("robopay_action_event", action_event_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load ActionEvent parser: {action_event_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_action_event


class Go2ZenohBridge:
    """Fail-closed Zenoh action bridge with correlated simulator results."""

    def __init__(self, model_dir: str | None = None, settings: BridgeSettings | None = None):
        try:
            import zenoh
        except ImportError as error:  # pragma: no cover - depends on optional runtime
            raise RuntimeError("Install eclipse-zenoh to run the Go2 bridge.") from error
        self._zenoh = zenoh
        self._model_dir = model_dir
        self.settings = settings or BridgeSettings.from_env()
        self.robot_id = self.settings.robot_id
        self.action_topic = self.settings.action_topic
        self.result_topic = self.settings.result_topic
        self.metrics_topic = self.settings.metrics_topic
        self._parse_action_event = _load_event_parser()
        self._session = _open_zenoh_session(self.settings)
        self._result_publisher = self._session.declare_publisher(self.result_topic)
        self._metrics_publisher = self._session.declare_publisher(self.metrics_topic)
        self._stop_event = threading.Event()
        self._stop_applied_event = threading.Event()
        self._worker_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._subscriber = self._session.declare_subscriber(self.action_topic, self._on_action)
        self._ready_publisher = self._session.declare_publisher(self.settings.ready_topic)
        self._ready_publisher.put(
            json.dumps(
                {
                    "status": "ready",
                    "profile_id": PROFILE_ID,
                    "robot_id": self.robot_id,
                    "action_topic": self.action_topic,
                    "result_topic": self.result_topic,
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
            "status": status,
            "profile_id": PROFILE_ID,
            "result": result,
        }
        payload = json.dumps(envelope).encode("utf-8")
        self._metrics_publisher.put(payload)
        self._result_publisher.put(payload)

    def _execute_navigation(
        self,
        event,
        max_duration: float,
        side: str,
        speed_scale: float,
        viewer: bool,
        viewer_hold_seconds: float | None,
    ) -> None:
        try:
            result = run_obstacle_nav(
                self._model_dir,
                max_duration,
                side,
                viewer=viewer,
                playback_rate=1.0,
                viewer_hold_seconds=viewer_hold_seconds,
                speed_scale=speed_scale,
                stop_requested=self._stop_event.is_set,
            )
        except Exception as error:  # keep the paid action terminal and non-settling
            LOGGER.exception("Go2 simulator execution failed")
            result = {
                "error_code": "SIMULATOR_EXECUTION_ERROR",
                "message": str(error),
                "success": False,
            }
        if result.get("safe_stop_applied"):
            self._stop_applied_event.set()
        self._publish(event, "success" if result.get("success") else "failure", result)

    def _on_action(self, sample) -> None:  # exercised by direct and live-Zenoh tests
        event = self._parse_action_event(bytes(sample.payload.to_bytes()))
        if event is None:
            LOGGER.error("Rejected malformed ActionEvent before simulation.")
            return
        # All robot profiles may share a Zenoh router during development.  A
        # foreign action is not ours to acknowledge: publishing a correlated
        # failure for another robot could race that robot's real bridge and
        # poison its paid action result.  Drop it silently before any result
        # or simulator interaction.
        if event.robot_id != self.robot_id:
            LOGGER.debug("Ignoring ActionEvent for foreign robot %s", event.robot_id)
            return
        action = event.action.lower()
        # The shared parser requires a complete correlation tuple. Keep the
        # bridge-side checks as a second boundary: a foreign or internally
        # inconsistent paid event is never allowed to reach MuJoCo.
        if event.action != event.skill_id:
            self._publish(event, "failure", {"error_code": "ACTION_SKILL_MISMATCH", "success": False})
            return
        if action not in ALLOWED_ACTIONS:
            self._publish(event, "failure", {"error_code": "UNREGISTERED_ACTION", "success": False})
            return

        if action == "stop":
            if event.params:
                self._publish(
                    event,
                    "failure",
                    {"error_code": "INVALID_PARAMS", "message": "stop does not accept parameters", "success": False},
                )
                return
            self._stop_event.set()
            with self._worker_lock:
                interrupted = self._worker is not None and self._worker.is_alive()
            stop_confirmed = not interrupted or self._stop_applied_event.wait(timeout=5.0)
            self._publish(
                event,
                "success" if stop_confirmed else "failure",
                {
                    "message": (
                        "Safe stop applied"
                        if stop_confirmed
                        else "Safe stop was not confirmed within 5 seconds"
                    ),
                    "error_code": None if stop_confirmed else "SAFE_STOP_TIMEOUT",
                    "safe_stop_applied": stop_confirmed,
                    "active_execution_interrupted": interrupted,
                    "success": stop_confirmed,
                },
            )
            return

        try:
            max_duration, side, speed_scale = _navigation_params(event.params)
        except ActionContractError as error:
            self._publish(event, "failure", {"error_code": error.code, "message": str(error), "success": False})
            return
        try:
            viewer, viewer_hold_seconds = _visual_payment_demo()
        except RuntimeError as error:
            LOGGER.error("Rejected paid visual-demo configuration: %s", error)
            self._publish(event, "failure", {"error_code": "INVALID_VIEWER_CONFIG", "success": False})
            return

        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                self._publish(event, "failure", {"error_code": "ROBOT_BUSY", "success": False})
                return
            self._stop_event.clear()
            self._stop_applied_event.clear()
            self._worker = threading.Thread(
                target=self._execute_navigation,
                args=(event, max_duration, side, speed_scale, viewer, viewer_hold_seconds),
                daemon=True,
                name=f"go2-action-{event.action_id}",
            )
            self._worker.start()

    def close(self) -> None:
        self._stop_event.set()
        with self._worker_lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=5)
        self._subscriber.undeclare()
        self._ready_publisher.undeclare()
        self._result_publisher.undeclare()
        self._metrics_publisher.undeclare()
        self._session.close()

    def spin(self) -> None:  # pragma: no cover - integration entry point
        LOGGER.info(
            "Go2 bridge %s listening on %s and publishing results on %s",
            self.robot_id,
            self.action_topic,
            self.result_topic,
        )
        try:
            while True:
                time.sleep(0.1)
        finally:
            self.close()


def main() -> None:  # pragma: no cover - integration entry point
    """Run the Go2 bridge as a standalone Zenoh worker."""

    logging.basicConfig(level=logging.INFO)
    Go2ZenohBridge().spin()


if __name__ == "__main__":
    main()
