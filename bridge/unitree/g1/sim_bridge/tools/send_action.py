"""Send a paid action to the bridge over Zenoh and print the result.

This is the payer's side of the demo. It builds a properly hashed envelope,
publishes it on the action topic, and waits for the correlated result.

The flags exist to exercise the acceptance criteria rather than to be
convenient:

    --unpaid     omit payment verification  -> expect PAYMENT_REQUIRED
    --tamper     edit a parameter after hashing -> expect PARAMS_HASH_MISMATCH
    --expired    backdate expiresAt         -> expect ACTION_EXPIRED
    --skill diagnostic_fail                 -> expect ACTION_FAILED
    --repeat 2   reuse the idempotency key  -> second attempt must not settle

Examples:

    python -m sim_bridge.tools.send_action --puck 0.34 -0.20 --goal 0.44 -0.04
    python -m sim_bridge.tools.send_action --unpaid
    python -m sim_bridge.tools.send_action --skill diagnostic_fail
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import zenoh

from ..g1.action_contract import canonical_params_hash

DEFAULT_ACTION_TOPIC = "robot/tunnel/action"
DEFAULT_RESULT_TOPIC = "robot/tunnel/result"


def build_envelope(
    robot_id: str,
    skill: str,
    params: dict[str, Any],
    idempotency_key: str,
    paid: bool,
    tamper: bool,
    expired: bool,
) -> dict[str, Any]:
    # Hash first, then optionally tamper, so the digest reflects what the payer
    # signed rather than what is actually sent.
    params_hash = canonical_params_hash(params)
    sent = dict(params)
    if tamper and "goal_x" in sent:
        sent["goal_x"] = round(float(sent["goal_x"]) + 0.05, 4)

    payment: dict[str, Any] = {
        "provider": "x402",
        "amount": "10000",
        "asset": "USDC",
        "network": "eip155:84532",
        "verified": paid,
    }
    if paid:
        # Stands in for the settlement reference the tunnel attaches once it
        # has verified the x402 authorisation.
        payment["txHash"] = "0x" + uuid.uuid4().hex

    envelope: dict[str, Any] = {
        "actionId": f"act_{uuid.uuid4().hex[:12]}",
        "robotId": robot_id,
        "skillId": skill,
        "params": sent,
        "idempotencyKey": idempotency_key,
        "paramsHash": params_hash,
        "payment": payment,
    }
    when = datetime.now(timezone.utc) + timedelta(
        minutes=-5 if expired else 10
    )
    envelope["expiresAt"] = when.isoformat()
    return envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-id", default=os.environ.get("ROBOT_ID", "g1-sim-001"))
    parser.add_argument("--skill", default="push_to_target")
    parser.add_argument("--puck", nargs=2, type=float, default=[0.34, -0.20],
                        metavar=("X", "Y"))
    parser.add_argument("--goal", nargs=2, type=float, default=[0.44, -0.04],
                        metavar=("X", "Y"))
    parser.add_argument("--unpaid", action="store_true")
    parser.add_argument("--tamper", action="store_true")
    parser.add_argument("--expired", action="store_true")
    parser.add_argument("--repeat", type=int, default=1,
                        help="send the same envelope N times to exercise replay")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--action-topic", default=DEFAULT_ACTION_TOPIC)
    parser.add_argument("--result-topic", default=DEFAULT_RESULT_TOPIC)
    parser.add_argument("--endpoint",
                        default=os.environ.get("ZENOH_ENDPOINT", "tcp/127.0.0.1:7447"),
                        help="the bridge's Zenoh endpoint")
    args = parser.parse_args(argv)

    params: dict[str, Any] = {}
    if args.skill == "push_to_target":
        params = {
            "puck_x": args.puck[0], "puck_y": args.puck[1],
            "goal_x": args.goal[0], "goal_y": args.goal[1],
        }

    config = zenoh.Config()
    if args.endpoint:
        config.insert_json5("connect/endpoints", json.dumps([args.endpoint]))

    results: list[dict[str, Any]] = []
    with zenoh.open(config) as session:
        session.declare_subscriber(
            args.result_topic,
            lambda s: results.append(json.loads(bytes(s.payload.to_bytes()))),
        )
        time.sleep(0.4)

        key = f"idem-{uuid.uuid4().hex[:10]}"
        exit_code = 0
        for attempt in range(1, args.repeat + 1):
            envelope = build_envelope(
                args.robot_id, args.skill, params, key,
                paid=not args.unpaid, tamper=args.tamper, expired=args.expired,
            )
            before = len(results)
            print(f"--- attempt {attempt}/{args.repeat}: "
                  f"action {envelope['actionId']} skill={args.skill} key={key}")
            session.put(args.action_topic, json.dumps(envelope).encode())

            deadline = time.time() + args.timeout
            while len(results) == before and time.time() < deadline:
                time.sleep(0.1)
            if len(results) == before:
                print("    no result within timeout")
                exit_code = 1
                continue

            result = results[-1]
            settle = result.get("settle")
            print(f"    status={result.get('status')} settle={settle}")
            if result.get("error"):
                print(f"    error={result['error']['code']}: "
                      f"{result['error']['message']}")
            if result.get("replayed"):
                print("    replayed=True (not re-executed, not settled)")
            metrics = result.get("metrics") or {}
            if metrics:
                print(f"    displacement={metrics.get('displacementM')}m "
                      f"final_distance={metrics.get('finalDistanceM')}m "
                      f"contacts={metrics.get('peakContacts')} "
                      f"sim={metrics.get('simSeconds')}s")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
