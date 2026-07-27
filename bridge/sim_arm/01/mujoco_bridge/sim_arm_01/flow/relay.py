"""Relay + robot node wired over an in-process transport.

Mirrors the real Zenoh pub/sub using the documented topic names, but runs
in-process so the whole flow is deterministic and reproducible in CI without a
Zenoh router. The real Zenoh runtime is in sim_arm_01/node.py.

Flow:
  relay.submit(action)
    -> PaymentGuard.verify_request (402 / 400 / 409 before any publish)
    -> publish to robot/tunnel/action, return {status: accepted}  (pending)
  RobotNode (subscribes robot/tunnel/action)
    -> execute_skill -> publish terminal ResultEnvelope to robot/tunnel/result
  relay (subscribes robot/tunnel/result)
    -> settle ONLY if status == success; correlate to caller by actionId
"""
import threading

from .envelope import (
    ACTION_TOPIC, RESULT_TOPIC, ActionEnvelope, ResultEnvelope,
)
from .payment import PaymentGuard, PaymentError
from .executor import execute_skill, ROBOT_ID


class InProcBus:
    """Minimal topic bus. Each published message is delivered to every
    subscriber on its own daemon thread, emulating async Zenoh delivery."""

    def __init__(self):
        self._subs: dict[str, list] = {}

    def subscribe(self, topic: str, callback) -> None:
        self._subs.setdefault(topic, []).append(callback)

    def publish(self, topic: str, payload: str) -> None:
        for cb in self._subs.get(topic, []):
            threading.Thread(target=cb, args=(payload,), daemon=True).start()


class RobotNode:
    """Robot control stack: subscribes to paid actions, executes the skill,
    publishes an actionId-correlated terminal result."""

    def __init__(self, bus: InProcBus):
        self._bus = bus
        bus.subscribe(ACTION_TOPIC, self._on_action)

    def _on_action(self, raw: str) -> None:
        action = ActionEnvelope.from_json(raw)
        if action.robotId != ROBOT_ID:
            return  # not addressed to this robot
        result = execute_skill(action)          # runs the real MuJoCo servo
        self._bus.publish(RESULT_TOPIC, result.to_json())


class RoboPayRelay:
    """Relay stand-in for the Fabric tunnel. Enforces payment safety, forwards
    paid actions, and settles only on a successful terminal result."""

    def __init__(self, bus: InProcBus, guard: PaymentGuard = None):
        self._bus = bus
        self._guard = guard or PaymentGuard()
        self._pending: dict[str, threading.Event] = {}
        self._results: dict[str, ResultEnvelope] = {}
        bus.subscribe(RESULT_TOPIC, self._on_result)

    def _on_result(self, raw: str) -> None:
        result = ResultEnvelope.from_json(raw)
        # Settlement gate: consume the terminal result, settle ONLY on success.
        if result.status == "success":
            self._guard.settle(result.actionId)
        self._results[result.actionId] = result
        event = self._pending.get(result.actionId)
        if event:
            event.set()

    def submit(self, action: ActionEnvelope) -> dict:
        """Verify safety, then publish. Returns accepted/pending, or a 4xx
        rejection that is NEVER published (robot never actuates)."""
        try:
            self._guard.verify_request(action)
        except PaymentError as e:
            return {"status": "rejected", "httpStatus": e.http_status,
                    "code": e.code, "message": e.message}

        self._pending[action.actionId] = threading.Event()
        self._bus.publish(ACTION_TOPIC, action.to_json())
        return {"status": "accepted", "actionId": action.actionId}

    def await_result(self, action_id: str, timeout: float = 30.0):
        """Block until the async terminal result arrives (or timeout).
        A timeout returns None and never settles."""
        event = self._pending.get(action_id)
        if event is None:
            return None
        if event.wait(timeout):
            return self._results.get(action_id)
        return None

    def is_settled(self, action_id: str) -> bool:
        return self._guard.is_settled(action_id)
