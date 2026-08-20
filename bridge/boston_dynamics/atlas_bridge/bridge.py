"""Zenoh bridge for paid Atlas shelf-inspection actions.

Action envelopes arrive on ``robot/tunnel/action``, are validated against the
registered skill contract, executed on the simulator, and answered on
``robot/tunnel/result`` with the originating ``action_id`` so the tunnel can
correlate the asynchronous result with the paid request.

The message handling lives in :class:`AtlasActionHandler`, which knows nothing
about transports. :class:`AtlasZenohBridge` only wires that handler to Zenoh.
The split exists so the full action path can be exercised by tests without a
live router — see ``tests/test_bridge_contract.py``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .idempotency import ConflictingRequest, IdempotencyStore
from .runner import run_inspection
from .task import EPISODE_BUDGET_S

LOGGER = logging.getLogger("robopay.atlas")
ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/boston_dynamics_atlas/metrics"
READY_TOPIC = "robot/boston_dynamics_atlas/ready"
ROBOT_ID = "atlas-sim-01"

#: Skills this bridge will execute, matching the registered profile.
INSPECT_ACTION = "inspect_shelf"
STOP_ACTION = "stop"
ALLOWED_ACTIONS = {INSPECT_ACTION, STOP_ACTION}
#: Identity a payment-validated action must carry. Without these the tunnel cannot correlate
#: a result with the request that paid for it, and the action cannot be
#: deduplicated, so it is refused rather than executed on a guess.
REQUIRED_IDENTITY_FIELDS = ("action_id", "robot_id", "skill_id", "idempotency_key")
#: Parameters declared for ``inspect_shelf`` in ``skills.yaml``.
INSPECTION_PARAMS = {"maxDurationSec"}
MIN_DURATION_S = 5.0
MAX_DURATION_S = 60.0

PROFILE_ID = "boston-dynamics.atlas.mujoco-pybullet-webots-shelf-inspection.v1"


def _payment_fingerprint(event) -> str:
    """Stable digest of the payment an action arrived with."""
    payload = getattr(event, "payment_payload", None)
    if not payload:
        return ""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class ActionContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def inspection_params(params: dict) -> float:
    """Validate ``inspect_shelf`` parameters and return the duration budget."""
    if not isinstance(params, dict):
        raise ActionContractError("INVALID_PARAMS", "params must be an object.")
    unexpected = sorted(set(params) - INSPECTION_PARAMS)
    if unexpected:
        raise ActionContractError(
            "INVALID_PARAMS", f"unregistered parameter(s): {', '.join(unexpected)}"
        )
    raw_duration = params.get("maxDurationSec", EPISODE_BUDGET_S)
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
        raise ActionContractError(
            "INVALID_DURATION",
            f"maxDurationSec must be a number from {MIN_DURATION_S:g} to {MAX_DURATION_S:g}.",
        )
    max_duration = float(raw_duration)
    if not math.isfinite(max_duration) or not MIN_DURATION_S <= max_duration <= MAX_DURATION_S:
        raise ActionContractError(
            "INVALID_DURATION",
            f"maxDurationSec must be between {MIN_DURATION_S:g} and {MAX_DURATION_S:g}.",
        )
    return max_duration


def load_event_parser():
    """Load the shared tunnel ActionEvent parser from ``bridge/common``."""
    action_event_path = (
        Path(__file__).resolve().parents[2]
        / "common" / "zenoh_bridge" / "zenoh_bridge" / "action_event.py"
    )
    spec = importlib.util.spec_from_file_location("robopay_action_event", action_event_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load ActionEvent parser: {action_event_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_action_event


class AtlasActionHandler:
    """Transport-free action path: envelope in, correlated result out.

    ``publish`` receives the encoded result envelope. ``execute`` runs the skill
    and defaults to the MuJoCo runner; tests substitute a fast stand-in.
    """

    def __init__(
        self,
        publish: Callable[[bytes], None],
        robot_id: str = ROBOT_ID,
        execute: Callable[..., dict] | None = None,
        synchronous: bool = False,
        idempotency: IdempotencyStore | None = None,
    ) -> None:
        self._publish_bytes = publish
        self.robot_id = robot_id
        self._execute = execute or run_inspection
        self._synchronous = synchronous
        self._idempotency = IdempotencyStore() if idempotency is None else idempotency
        self._parse_action_event = load_event_parser()
        self._stop_event = threading.Event()
        self._stop_applied_event = threading.Event()
        self._worker_lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # -- result publication -------------------------------------------------
    def _publish(self, event, status: str, result: dict) -> dict:
        """Answer an action, echoing every field the tunnel correlates on."""
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
        self._publish_bytes(json.dumps(envelope).encode("utf-8"))
        return envelope

    # -- skill execution ----------------------------------------------------
    def _run_inspection(self, event, max_duration: float) -> None:
        status = "failure"
        try:
            result = self._execute(
                max_duration_seconds=max_duration,
                stop_requested=self._stop_event.is_set,
            )
        except Exception as error:  # noqa: BLE001 - reported, never swallowed
            LOGGER.exception("Atlas simulator execution failed")
            result = {
                "error_code": "SIMULATOR_EXECUTION_ERROR",
                "message": str(error),
                "success": False,
            }
        if result.get("safe_stop_applied"):
            self._stop_applied_event.set()
            status = "safe_stopped"
        elif result.get("success"):
            status = "success"
        self._idempotency.complete(
            event.robot_id, event.skill_id, event.idempotency_key, status
        )
        self._publish(event, "success" if result.get("success") else "failure", result)

    def _handle_stop(self, event) -> None:
        if event.params:
            self._publish(event, "failure", {
                "error_code": "INVALID_PARAMS",
                "message": "stop does not accept parameters",
                "success": False,
            })
            return
        self._stop_event.set()
        with self._worker_lock:
            interrupted = self._worker is not None and self._worker.is_alive()
        stop_confirmed = not interrupted or self._stop_applied_event.wait(timeout=5.0)
        self._publish(event, "success" if stop_confirmed else "failure", {
            "message": (
                "Safe stop applied" if stop_confirmed
                else "Safe stop was not confirmed within 5 seconds"
            ),
            "error_code": None if stop_confirmed else "SAFE_STOP_TIMEOUT",
            "safe_stop_applied": stop_confirmed,
            "active_execution_interrupted": interrupted,
            "success": stop_confirmed,
        })

    # -- entry point --------------------------------------------------------
    def handle(self, payload: bytes) -> str | None:
        """Process one raw action envelope. Returns the outcome for tests."""
        event = self._parse_action_event(payload)
        if event is None:
            LOGGER.error("Rejected malformed ActionEvent before simulation.")
            return "rejected_malformed"
        if event.robot_id != self.robot_id:
            LOGGER.debug("Ignoring ActionEvent for foreign robot %s", event.robot_id)
            return "ignored_foreign_robot"

        missing = [name for name in REQUIRED_IDENTITY_FIELDS if not getattr(event, name, "")]
        if missing:
            self._publish(event, "failure", {
                "error_code": "MISSING_IDENTITY",
                "message": f"action envelope is missing {', '.join(missing)}",
                "success": False,
            })
            return "failure"

        action = event.action
        if event.action != event.skill_id:
            self._publish(event, "failure", {"error_code": "ACTION_SKILL_MISMATCH", "success": False})
            return "failure"
        if action not in ALLOWED_ACTIONS:
            self._publish(event, "failure", {"error_code": "UNREGISTERED_ACTION", "success": False})
            return "failure"

        if action == STOP_ACTION:
            self._handle_stop(event)
            return "stop"

        try:
            max_duration = inspection_params(event.params)
        except ActionContractError as error:
            self._publish(event, "failure", {
                "error_code": error.code, "message": str(error), "success": False,
            })
            return "failure"

        # A payment-validated action actuates the robot once. The same key replays its
        # recorded outcome; the same key describing a different request is a
        # conflict, not a retry.
        fingerprint = _payment_fingerprint(event)
        try:
            previous = self._idempotency.claim(
                event.robot_id, event.skill_id, event.idempotency_key,
                event.params_hash, fingerprint, event.action_id,
            )
        except ConflictingRequest as conflict:
            self._publish(event, "failure", {
                "error_code": conflict.code, "message": str(conflict), "success": False,
            })
            return "failure"
        if previous is not None:
            self._publish(event, "duplicate", {
                "error_code": "DUPLICATE_ACTION",
                "message": (
                    f"idempotency key {event.idempotency_key!r} already actuated "
                    f"this robot as {previous.action_id}"
                ),
                "first_action_id": previous.action_id,
                "first_status": previous.status,
                "success": False,
            })
            return "duplicate"

        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                self._publish(event, "failure", {"error_code": "ROBOT_BUSY", "success": False})
                return "failure"
            self._stop_event.clear()
            self._stop_applied_event.clear()
            if self._synchronous:
                self._run_inspection(event, max_duration)
                return "executed"
            self._worker = threading.Thread(
                target=self._run_inspection,
                args=(event, max_duration),
                daemon=True,
                name=f"atlas-action-{event.action_id}",
            )
            self._worker.start()
        return "accepted"

    def wait_for_idle(self, timeout: float = 120.0) -> bool:
        with self._worker_lock:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout=timeout)
        return not worker.is_alive()

    def request_stop(self) -> None:
        self._stop_event.set()


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


def _open_zenoh_session(settings: BridgeSettings):
    import zenoh

    if settings.zenoh_config_path:
        return zenoh.open(zenoh.Config.from_file(settings.zenoh_config_path))
    if settings.zenoh_endpoint:
        config = zenoh.Config.from_json5(
            json.dumps({
                "mode": "client",
                "connect": {"endpoints": [settings.zenoh_endpoint]},
            })
        )
        return zenoh.open(config)
    return zenoh.open(zenoh.Config())


class AtlasZenohBridge:
    """Wires :class:`AtlasActionHandler` onto the RoboPay tunnel topics."""

    def __init__(
        self,
        settings: BridgeSettings | None = None,
        execute: Callable[..., dict] | None = None,
    ):
        """``execute`` replaces the default episode runner.

        The recorder in ``evidence_recording.py`` uses it to render the very
        episode the paid action triggers, rather than a second one run
        afterwards — a recording of a different episode would not be evidence
        of this one.
        """
        try:
            import zenoh
        except ImportError as error:
            raise RuntimeError("Install eclipse-zenoh to run the Atlas bridge.") from error
        self._zenoh = zenoh
        self.settings = settings or BridgeSettings.from_env()
        self.robot_id = self.settings.robot_id
        self.action_topic = self.settings.action_topic
        self.result_topic = self.settings.result_topic
        self.metrics_topic = self.settings.metrics_topic

        self._session = _open_zenoh_session(self.settings)
        self._result_publisher = self._session.declare_publisher(self.result_topic)
        self._metrics_publisher = self._session.declare_publisher(self.metrics_topic)
        self.handler = AtlasActionHandler(
            self._publish_result, execute=execute, robot_id=self.robot_id
        )
        self._subscriber = self._session.declare_subscriber(self.action_topic, self._on_action)
        self._ready_publisher = self._session.declare_publisher(self.settings.ready_topic)
        self._ready_publisher.put(
            json.dumps({
                "status": "ready",
                "profile_id": PROFILE_ID,
                "robot_id": self.robot_id,
                "skills": sorted(ALLOWED_ACTIONS),
                "action_topic": self.action_topic,
                "result_topic": self.result_topic,
            }, separators=(",", ":")).encode("utf-8")
        )

    def _publish_result(self, payload: bytes) -> None:
        self._metrics_publisher.put(payload)
        self._result_publisher.put(payload)

    def _on_action(self, sample) -> None:
        self.handler.handle(bytes(sample.payload.to_bytes()))

    def close(self) -> None:
        self.handler.request_stop()
        self.handler.wait_for_idle(timeout=5.0)
        self._subscriber.undeclare()
        self._ready_publisher.undeclare()
        self._result_publisher.undeclare()
        self._metrics_publisher.undeclare()
        self._session.close()

    def spin(self) -> None:
        LOGGER.info(
            "Atlas bridge %s listening on %s and publishing results on %s",
            self.robot_id, self.action_topic, self.result_topic,
        )
        try:
            while True:
                time.sleep(0.1)
        finally:
            self.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    AtlasZenohBridge().spin()


if __name__ == "__main__":
    main()
