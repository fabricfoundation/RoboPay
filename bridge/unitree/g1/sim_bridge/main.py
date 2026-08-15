"""Zenoh bridge: paid actions in, robot results out.

Topics (all configurable; these are the defaults the README documents):

    robot/tunnel/action     subscribe   paid action envelopes from the tunnel
    robot/tunnel/result     publish     terminal result, correlated by actionId
    robot/g1/metrics        publish     simulator state metrics for the run
    robot/g1/skills         queryable   skill catalogue, for pre-purchase discovery

The bridge itself does no payment verification. That is the tunnel's job, and
keeping the split honest matters: the tunnel proves the money is real, the
bridge proves the robot did the work, and neither gets to vouch for the other.
What the bridge does enforce is that nothing reaches the robot unless the
envelope is well formed, addressed to this robot, unexpired, unmodified since
the payer signed it, and not a replay.

Run it with:

    python -m sim_bridge.main --robot-id g1-sim-001

and send it work with `tools/send_action.py`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from typing import Any

import zenoh

from .g1.action_contract import ActionEnvelope, ActionRejected
from .g1.mapper import catalogue
from .g1.node import ActionNode, ExecutionResult, IdempotencyStore
from .simulation.runner import TaskRunner

LOG = logging.getLogger("robopay.g1")

DEFAULT_ACTION_TOPIC = "robot/tunnel/action"
DEFAULT_RESULT_TOPIC = "robot/tunnel/result"
DEFAULT_METRICS_TOPIC = "robot/g1/metrics"
DEFAULT_SKILLS_TOPIC = "robot/g1/skills"


class Bridge:
    """Wires Zenoh to the action node."""

    def __init__(
        self,
        session: zenoh.Session,
        node: ActionNode,
        result_topic: str = DEFAULT_RESULT_TOPIC,
        metrics_topic: str = DEFAULT_METRICS_TOPIC,
    ) -> None:
        self._session = session
        self._node = node
        self._result_topic = result_topic
        self._metrics_topic = metrics_topic

    def on_action(self, sample: Any) -> None:
        """Handle one incoming envelope. Never raises into the Zenoh runtime."""
        try:
            payload = bytes(sample.payload.to_bytes())
        except Exception as exc:  # noqa: BLE001
            LOG.error("could not read Zenoh payload: %s", exc)
            return

        try:
            envelope = ActionEnvelope.from_bytes(payload)
        except ActionRejected as rejected:
            LOG.warning("rejected before execution: %s -- %s",
                        rejected.code, rejected.message)
            self._publish(
                ExecutionResult.failure(
                    action_id="unknown",
                    skill="unknown",
                    code=rejected.code,
                    message=rejected.message,
                )
            )
            return

        LOG.info(
            "action %s skill=%s params=%s idem=%s",
            envelope.action_id, envelope.skill_id,
            json.dumps(envelope.params, sort_keys=True), envelope.idempotency_key,
        )
        result = self._node.handle(envelope)
        if result.status == "success":
            LOG.info("action %s SUCCESS settle=%s", result.action_id, result.settle)
        else:
            LOG.warning(
                "action %s FAILED code=%s settle=%s -- %s",
                result.action_id,
                (result.error or {}).get("code"),
                result.settle,
                (result.error or {}).get("message"),
            )
        self._publish(result)

    def _publish(self, result: ExecutionResult) -> None:
        body = result.to_json()
        self._session.put(self._result_topic, json.dumps(body).encode())
        if result.metrics:
            self._session.put(
                self._metrics_topic,
                json.dumps(
                    {"actionId": result.action_id, "metrics": result.metrics}
                ).encode(),
            )


def build_session(listen: str | None, connect: str | None) -> zenoh.Session:
    """Open a Zenoh session.

    The bridge listens on an explicit TCP endpoint by default rather than
    relying on multicast scouting. Peer discovery by multicast does not work
    in every environment -- it silently does not here -- and a demo that
    depends on it looks like a hung bridge rather than a networking problem.
    A fixed endpoint is also what the README can tell a reviewer to use.
    """
    raw = os.environ.get("ZENOH_CONFIG")
    if raw:
        return zenoh.open(zenoh.Config.from_json5(raw))
    config = zenoh.Config()
    if listen:
        config.insert_json5("listen/endpoints", json.dumps([listen]))
    if connect:
        config.insert_json5("connect/endpoints", json.dumps([connect]))
    return zenoh.open(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-id", default=os.environ.get("ROBOT_ID", "g1-sim-001"))
    parser.add_argument("--action-topic",
                        default=os.environ.get("ZENOH_ACTION_TOPIC", DEFAULT_ACTION_TOPIC))
    parser.add_argument("--result-topic",
                        default=os.environ.get("ZENOH_RESULT_TOPIC", DEFAULT_RESULT_TOPIC))
    parser.add_argument("--metrics-topic",
                        default=os.environ.get("ZENOH_METRICS_TOPIC", DEFAULT_METRICS_TOPIC))
    parser.add_argument("--listen",
                        default=os.environ.get("ZENOH_LISTEN", "tcp/127.0.0.1:7447"),
                        help="endpoint this bridge accepts connections on")
    parser.add_argument("--connect", default=os.environ.get("ZENOH_CONNECT"),
                        help="optional upstream router to dial out to")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    runner = TaskRunner()
    node = ActionNode(args.robot_id, runner, IdempotencyStore())

    with build_session(args.listen, args.connect) as session:
        bridge = Bridge(session, node, args.result_topic, args.metrics_topic)
        subscriber = session.declare_subscriber(args.action_topic, bridge.on_action)
        # Publish the catalogue once so a payer can discover skills and prices
        # before deciding to buy anything.
        session.put(
            DEFAULT_SKILLS_TOPIC,
            json.dumps({"robotId": args.robot_id,
                        "skills": catalogue(args.robot_id)}).encode(),
        )
        LOG.info("zenoh endpoint %s", args.listen)
        LOG.info("robot %s listening on %s", args.robot_id, args.action_topic)
        LOG.info("results -> %s, metrics -> %s", args.result_topic, args.metrics_topic)

        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        stop.wait()
        LOG.info("shutting down")
        subscriber.undeclare()
    return 0


if __name__ == "__main__":
    sys.exit(main())
