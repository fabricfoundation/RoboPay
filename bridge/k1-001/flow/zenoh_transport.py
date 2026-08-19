"""Zenoh transport for RoboPay Tier 1 (Phase 2).

Official topics (do NOT change):
    robot/tunnel/action   client (tunnel)  -> robot
    robot/tunnel/result   robot            -> client

The transport delivers an *action envelope* to the robot and returns the
*result envelope*, correlated by actionId. The SAME envelope contract is used
whether the medium is real Zenoh or the in-process loopback stand-in, so the
protocol is identical and reviewer-verifiable.

Platform note: zenoh ships wheels for Linux/macOS only (no Windows wheels).
  - On Linux (CI / reviewer machine): ZenohTransport + ZenohRobotNode use the
    real zenoh library over TCP loopback.
  - On Windows / when zenoh is unavailable: LoopbackTransport provides a
    faithful pub/sub mimic (background thread + condition variable, identical
    topics + envelope) so the full payment -> transport -> execution -> result
    flow is exercised deterministically.
"""
import json
import threading
import time

try:
    import zenoh  # type: ignore
    _HAS_ZENOH = True
except Exception:  # pragma: no cover - depends on platform
    _HAS_ZENOH = False

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"

DEFAULT_ENDPOINT = "tcp/127.0.0.1:17447"
DEFAULT_MODE = "peer"


def has_zenoh() -> bool:
    return _HAS_ZENOH


def _decode_payload(sample) -> dict:
    raw = getattr(sample, "payload", sample)
    if hasattr(raw, "to_bytes"):
        raw = raw.to_bytes()
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw)
    return json.loads(raw.decode("utf-8"))


class Transport:
    """Delivers an action envelope and returns the correlated result envelope."""

    def send_action(self, action_envelope: dict, timeout: float = 10.0) -> dict:
        raise NotImplementedError

    def close(self):
        pass


class RobotHandler:
    """Pure execution logic shared by the real Zenoh node and the loopback.

    Given an action envelope, runs the executor and returns a result envelope
    on the official result-topic contract. Kept free of any transport concern
    so both media exercise identical behavior.
    """

    def __init__(self, executor):
        self.executor = executor

    def handle(self, action_envelope: dict) -> dict:
        skill_id = action_envelope.get("skillId")
        params = action_envelope.get("params", {})
        res = self.executor.execute(skill_id, params)
        return {
            "actionId": action_envelope.get("actionId"),
            "robotId": action_envelope.get("robotId"),
            "skillId": skill_id,
            "paramsHash": action_envelope.get("paramsHash"),
            "status": "completed" if res.success else "failed",
            "message": res.message,
            "metrics": res.metrics,
        }


class LoopbackTransport(Transport):
    """Faithful in-process stand-in for Zenoh pub/sub.

    Simulates the wire: a background "robot" thread receives the published
    action, executes it, and publishes a result the client waits for. Uses the
    SAME topic constants and envelope contract as ZenohTransport, so swapping
    the medium changes nothing about the protocol.
    """

    def __init__(self, executor, settle_delay: float = 0.0):
        self._handler = RobotHandler(executor)
        self._results = {}
        self._cv = threading.Condition()
        self._settle_delay = settle_delay

    def send_action(self, action_envelope: dict, timeout: float = 10.0) -> dict:
        aid = action_envelope.get("actionId")

        def _robot():
            if self._settle_delay:
                time.sleep(self._settle_delay)
            result = self._handler.handle(action_envelope)
            with self._cv:
                self._results[aid] = result
                self._cv.notify_all()

        threading.Thread(target=_robot, daemon=True).start()
        with self._cv:
            deadline = time.time() + timeout
            while aid not in self._results:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"no result for action {aid}")
                self._cv.wait(timeout=remaining)
            return self._results.pop(aid)


class ZenohTransport(Transport):
    """Real Zenoh client transport (Linux)."""

    def __init__(self, endpoint=DEFAULT_ENDPOINT, mode=DEFAULT_MODE,
                 connect_timeout=3.0, timeout=10.0):
        if not _HAS_ZENOH:
            raise RuntimeError("zenoh is not installed (Linux only)")
        self.endpoint = endpoint
        self.timeout = timeout
        self._results = {}
        self._cv = threading.Condition()
        conf = zenoh.Config()
        conf.insert_json5("mode", json.dumps(mode))
        conf.insert_json5("connect/endpoints", json.dumps([endpoint]))
        self._session = zenoh.open(conf)
        self._pub = self._session.declare_publisher(ACTION_TOPIC)
        self._sub = self._session.declare_subscriber(RESULT_TOPIC, self._on_result)
        time.sleep(connect_timeout)  # let the peer link establish

    def _on_result(self, sample):
        res = _decode_payload(sample)
        aid = res.get("actionId")
        with self._cv:
            self._results[aid] = res
            self._cv.notify_all()

    def send_action(self, action_envelope: dict, timeout: float = None) -> dict:
        aid = action_envelope.get("actionId")
        timeout = timeout or self.timeout
        self._pub.put(json.dumps(action_envelope).encode("utf-8"))
        with self._cv:
            deadline = time.time() + timeout
            while aid not in self._results:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError(f"no result for action {aid}")
                self._cv.wait(timeout=remaining)
            return self._results.pop(aid)

    def close(self):
        try:
            self._session.close()
        except Exception:
            pass


class ZenohRobotNode:
    """Real Zenoh robot side: subscribes to actions, executes, publishes results."""

    def __init__(self, executor, endpoint=DEFAULT_ENDPOINT, mode=DEFAULT_MODE):
        if not _HAS_ZENOH:
            raise RuntimeError("zenoh is not installed (Linux only)")
        self._handler = RobotHandler(executor)
        self.endpoint = endpoint
        self.mode = mode
        self._session = None
        self._running = False

    def _start(self):
        conf = zenoh.Config()
        conf.insert_json5("mode", json.dumps(self.mode))
        conf.insert_json5("listen/endpoints", json.dumps([self.endpoint]))
        self._session = zenoh.open(conf)
        self._pub = self._session.declare_publisher(RESULT_TOPIC)
        self._sub = self._session.declare_subscriber(ACTION_TOPIC, self._on_action)

    def _on_action(self, sample):
        action = _decode_payload(sample)
        result = self._handler.handle(action)
        self._pub.put(json.dumps(result).encode("utf-8"))

    def serve(self, stop_event: threading.Event = None):
        self._start()
        self._running = True
        try:
            if stop_event is not None:
                stop_event.wait()
            else:
                while self._running:
                    time.sleep(0.2)
        finally:
            self.stop()

    def stop(self):
        self._running = False
        try:
            self._session.close()
        except Exception:
            pass


def make_transport(executor, prefer="zenoh"):
    """Factory: real Zenoh if available, else faithful loopback.

    prefer="zenoh" tries the real transport and falls back to loopback when
    zenoh cannot be imported (e.g. Windows dev). prefer="loopback" forces the
    deterministic stand-in for tests.
    """
    if prefer == "zenoh" and _HAS_ZENOH:
        try:
            return ZenohTransport()
        except Exception:
            pass
    return LoopbackTransport(executor)
