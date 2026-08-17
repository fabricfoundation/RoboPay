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

    python -m sim_bridge.tools.send_action --puck 0.26 0.17 --goal 0.27 0.30
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

from ..x2.action_contract import canonical_params_hash

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


def wrap_as_tunnel_message(envelope: dict[str, Any], paid: bool) -> dict[str, Any]:
    """Re-shape a flat envelope into what the Go tunnel actually publishes.

    Reproduced from tunnel/internal/handlers/handlers.go: `POST /action` sits
    behind the x402 middleware, and on a verified payment the handler wraps the
    client's body in `payload`, attaches the resolved x402 payload and
    requirements under `transaction_details`, and publishes that.

    Two details matter and are easy to get wrong:

      * the body carries no payment block at all -- the client never sends one,
        the middleware resolves it; and
      * there is no transaction hash. x402 verifies, runs the handler, and
        settles afterwards, so at this point the payment is verified and
        unsettled. That is the whole reason `settle` is the robot's to report.

    An unpaid request never reaches the handler in the real tunnel, so it is
    never published at all. `--unpaid --tunnel-format` models the weaker case
    that is still worth refusing: something reaching the topic directly with no
    payment payload on it.
    """
    body = {k: v for k, v in envelope.items() if k != "payment"}
    requirements = {
        "scheme": "exact",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",  # USDC, Base Sepolia
        "amount": "2000",
        "payTo": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "maxTimeoutSeconds": 30,
    }
    details: dict[str, Any] = {"payment_requirements": requirements}
    if paid:
        details["payment_payload"] = {
            "x402Version": 2,
            "scheme": "exact",
            "network": "eip155:84532",
            "payload": {
                "signature": "0x" + uuid.uuid4().hex * 2 + uuid.uuid4().hex[:2],
                "authorization": {
                    "from": "0x1111111111111111111111111111111111111111",
                    "to": requirements["payTo"],
                    "value": requirements["amount"],
                    "validAfter": "0",
                    "validBefore": "9999999999",
                    "nonce": "0x" + uuid.uuid4().hex + uuid.uuid4().hex,
                },
            },
            "accepted": requirements,
        }
    return {
        "payload": body,
        "transaction_details": details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot-id", default=os.environ.get("ROBOT_ID", "x2-sim-001"))
    parser.add_argument("--skill", default="push_to_target")
    parser.add_argument("--puck", nargs=2, type=float, default=[0.26, 0.17],
                        metavar=("X", "Y"))
    parser.add_argument("--goal", nargs=2, type=float, default=[0.27, 0.30],
                        metavar=("X", "Y"))
    parser.add_argument("--unpaid", action="store_true")
    parser.add_argument("--tamper", action="store_true")
    parser.add_argument("--expired", action="store_true")
    parser.add_argument(
        "--tunnel-format", action="store_true",
        help="publish the wrapper the Go tunnel really puts on the topic "
             "({payload, transaction_details, timestamp}) instead of a flat "
             "envelope, to exercise the bridge against the actual contract",
    )
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
            message: dict[str, Any] = envelope
            if args.tunnel_format:
                message = wrap_as_tunnel_message(envelope, paid=not args.unpaid)

            before = len(results)
            shape = "tunnel wrapper" if args.tunnel_format else "flat envelope"
            print(f"--- attempt {attempt}/{args.repeat}: "
                  f"action {envelope['actionId']} skill={args.skill} key={key} "
                  f"[{shape}]")
            session.put(args.action_topic, json.dumps(message).encode())

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
