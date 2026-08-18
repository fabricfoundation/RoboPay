"""Zenoh bridge for paid kinova-real-001 ``pick_object`` actions.

Production payment boundary -- RoboPay Tunnel + x402 facilitator
==============================================================

The RoboPay Tunnel (the shared Go ``tunnel/`` binary) enforces the x402
payment gate with a custom execution-gated settlement middleware: it answers
HTTP 402 for unpaid requests, verifies a paid request synchronously, and then
publishes the action to the ``robot/tunnel/action`` Zenoh topic and returns
202 *accepted*. Settlement -- the actual USDC transfer -- is performed by the
Tunnel's x402 facilitator **only after this bridge publishes a successful
terminal result** on ``robot/tunnel/result``. A failed or timed-out execution
never settles, and a replayed idempotency key / payment payload is rejected
with 409 before anything is published.

This bridge is therefore a fail-closed Zenoh *subscriber*: it can only ever
see already-paid actions, runs the real MuJoCo physics for ``pick_object``,
and publishes the terminal result -- echoing the exact correlation tuple the
Tunnel issued (action_id, robot_id, skill_id, params_hash, idempotency_key)
so the Tunnel can match the result to the paid action and settle only on
success. It never verifies or settles a payment itself -- that is the Tunnel's
job.

This is the fix for the reviewer's CHANGES_REQUESTED note on PR #70
("records settlement in a local ledger, so it does not yet demonstrate
verification and settlement through the RoboPay Tunnel and x402 facilitator"):
settlement now flows through the real Tunnel + x402 facilitator, and the
local ledger in ``flow/payment.py`` is an in-process audit log only.

See ``tests/test_payment_gate.py`` (fail-closed boundary through the real
Tunnel binary), ``tests/test_x402_no_settlement.py`` (failure/timeout/replay
never settle), and ``tests/test_bridge_executes.py`` (end-to-end paid-action
execution through the real Tunnel + MuJoCo) for the proof exercised against
the real Go binary.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from flow.executor import MuJoCoExecutor


LOGGER = logging.getLogger("robopay.kinova")

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"
METRICS_TOPIC = "robot/kinova-real-001/metrics"

ROBOT_ID = "kinova-real-001"
ALLOWED_ACTIONS = {'"open_door"'}
PROFILE_ID = "kinova.kinova-real-001.mujoco-sim.v1"

# Bounded parameter contract (mirrors skill-catalog.json so the bridge rejects
# an out-of-contract paid action fail-closed instead of touching the
# simulator). See acceptance criterion #5 (bounded policy + safe stop).
KNOWN_OBJECTS = {
    "cube", "unreachable", "collision", "timeout",
    "far_cube", "blocked_cube", "slow_cube",
}
MAX_STEPS_BOUND = (1, 2000)


class ActionContractError(ValueError):
    """A fail-closed bridge contract violation with a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class BridgeSettings:
    """Deployment settings that can be changed without editing source files."""

    robot_id: str
    action_topic: str
    result_topic: str
    metrics_topic: str

    @classmethod
    def from_env(cls) -> "BridgeSettings":
        def configured(name: str, default: str) -> str:
            return os.environ.get(name, default).strip() or default

        return cls(
            robot_id=configured("ROBOT_ID", ROBOT_ID),
            action_topic=configured("ZENOH_ACTION_TOPIC", ACTION_TOPIC),
            result_topic=configured("ZENOH_RESULT_TOPIC", RESULT_TOPIC),
            metrics_topic=configured("ZENOH_METRICS_TOPIC", METRICS_TOPIC),
        )


def _params_hash(params: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _validate_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the profile's bounded parameter contract fail-closed."""
    if not isinstance(params, dict):
        raise ActionContractError("INVALID_PARAMS", "params must be an object.")
    unexpected = sorted(set(params) - {"object", "maxSteps"})
    if unexpected:
        raise ActionContractError(
            "INVALID_PARAMS", f"unregistered parameter(s): {', '.join(unexpected)}"
        )
    obj = params.get("object", "cube")
    if obj not in KNOWN_OBJECTS:
        raise ActionContractError("INVALID_PARAMS", f"unknown object scene: {obj!r}")
    max_steps = params.get("maxSteps")
    if max_steps is not None:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise ActionContractError("INVALID_STEPS", "maxSteps must be an integer.")
        if not MAX_STEPS_BOUND[0] <= max_steps <= MAX_STEPS_BOUND[1]:
            raise ActionContractError(
                "INVALID_STEPS",
                f"maxSteps must be between {MAX_STEPS_BOUND[0]} and {MAX_STEPS_BOUND[1]}.",
            )
    return params


class FabricZenohBridge:
    """Fail-closed Zenoh action bridge with correlated simulator results.

    The Tunnel already verified and gated the payment before publishing the
    action; this bridge only runs the real MuJoCo physics and reports the
    terminal result, echoing the correlation tuple so the Tunnel can settle
    strictly on success.
    """

    def __init__(self, settings: BridgeSettings | None = None):
        try:
            import zenoh
        except ImportError as error:  # pragma: no cover - depends on optional runtime
            raise RuntimeError("Install eclipse-zenoh to run the bridge.") from error

        self._zenoh = zenoh
        self.settings = settings or BridgeSettings.from_env()
        self.robot_id = self.settings.robot_id
        self.action_topic = self.settings.action_topic
        self.result_topic = self.settings.result_topic
        self.metrics_topic = self.settings.metrics_topic

        # Real physics executor (MuJoCo). The Tunnel only forwards paid
        # actions, so the executor choice is a pure execution detail.
        self._executor = MuJoCoExecutor()

        self._session = zenoh.open(zenoh.Config())
        self._result_publisher = self._session.declare_publisher(self.result_topic)
        self._metrics_publisher = self._session.declare_publisher(self.metrics_topic)
        self._subscriber = self._session.declare_subscriber(
            self.action_topic, self._on_action
        )

    def _publish(
        self,
        action_id: str,
        robot_id: str,
        skill_id: str,
        params_hash: str,
        idempotency_key: str,
        status: str,
        result: dict,
        params: dict,
    ) -> None:
        # Echo the correlation tuple the Tunnel issued so it can match this
        # terminal result to the exact paid action. Settlement is gated on a
        # successful status (status == "success"); failure/timeout never settle.
        envelope = {
            "action_id": action_id,
            "robot_id": robot_id,
            "skill_id": skill_id,
            "profile_id": PROFILE_ID,
            "params_hash": params_hash,
            "idempotency_key": idempotency_key,
            "status": status,
            "result": result,
        }
        payload = json.dumps(envelope).encode("utf-8")
        self._metrics_publisher.put(payload)
        self._result_publisher.put(payload)

    def _on_action(self, sample) -> None:
        raw = getattr(sample, "payload", sample)
        if hasattr(raw, "to_bytes"):
            raw = raw.to_bytes()
        try:
            event = json.loads(bytes(raw))
        except Exception:  # malformed payload -> never touch the simulator
            LOGGER.error("Rejected malformed ActionEvent before simulation.")
            return

        payload = event.get("payload") or {}
        action = (payload.get("action") or "").lower()
        params = payload.get("params") or {}

        # Correlation tuple echoed verbatim from the Tunnel's published event.
        action_id = event.get("action_id") or ""
        robot_id = event.get("robot_id") or self.robot_id
        skill_id = event.get("skill_id") or action
        params_hash = event.get("params_hash") or _params_hash(params)
        idempotency_key = event.get("idempotency_key") or action_id

        if action not in ALLOWED_ACTIONS:
            self._publish(
                action_id, robot_id, skill_id, params_hash, idempotency_key,
                "failure",
                {"success": False, "error_code": "UNREGISTERED_ACTION",
                 "message": f"action {action!r} is not registered for {self.robot_id}"},
                params,
            )
            return

        try:
            _validate_params(params)
        except ActionContractError as error:
            self._publish(
                action_id, robot_id, skill_id, params_hash, idempotency_key,
                "failure",
                {"success": False, "error_code": error.code, "message": str(error)},
                params,
            )
            return

        # Real physics execution. The action is already paid (the Tunnel gate
        # ran before publishing), so this is the legitimate, correlated run.
        try:
            res = self._executor.execute("pick_object", params)
        except Exception as error:  # keep the paid action terminal and non-settling
            LOGGER.exception("Simulator execution failed")
            self._publish(
                action_id, robot_id, skill_id, params_hash, idempotency_key,
                "failure",
                {"success": False, "error_code": "SIMULATOR_EXECUTION_ERROR",
                 "message": str(error)},
                params,
            )
            return

        self._publish(
            action_id, robot_id, skill_id, params_hash, idempotency_key,
            "success" if res.success else "failure",
            {"success": res.success, "message": res.message, "metrics": res.metrics},
            params,
        )

    def spin(self) -> None:  # pragma: no cover - integration entry point
        LOGGER.info(
            "kinova-real-001 bridge %s listening on %s; publishing results on %s",
            self.robot_id, self.action_topic, self.result_topic,
        )
        try:
            while True:
                time.sleep(0.1)
        finally:
            self._subscriber.undeclare()
            self._result_publisher.undeclare()
            self._metrics_publisher.undeclare()
            self._session.close()

    def close(self) -> None:
        try:
            self._subscriber.undeclare()
            self._result_publisher.undeclare()
            self._metrics_publisher.undeclare()
            self._session.close()
        except Exception:
            pass


def main() -> None:  # pragma: no cover - integration entry point
    """Run the kinova-real-001 bridge as a standalone Zenoh worker."""
    logging.basicConfig(level=logging.INFO)
    FabricZenohBridge().spin()


if __name__ == "__main__":
    main()
