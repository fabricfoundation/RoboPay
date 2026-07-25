#!/usr/bin/env python3
"""Demo script showing end-to-end paid action flow for G1 MuJoCo simulation.

Usage:
    # Start tunnel first, then bridge, then run this demo.
    python demo/run_demo.py --skill move_forward
    python demo/run_demo.py --skill navigate_obstacle --goal-x 5 --goal-y 3
    python demo/run_demo.py --skill stop
    python demo/run_demo.py --skill move_forward --no-pay  # expect 402
"""

import argparse
import json
import logging
import sys
import time
import uuid

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DEMO] %(message)s",
)
logger = logging.getLogger(__name__)

try:
    import zenoh
except ImportError:
    logger.error("zenoh-py not installed. Run: pip install zenoh-py")
    sys.exit(1)


def create_action_envelope(robot_id: str, skill_id: str, params: dict, paid: bool = True) -> dict:
    """Create a Fabric action envelope."""
    envelope = {
        "actionId": f"act_{skill_id}_{uuid.uuid4().hex[:8]}",
        "robotId": robot_id,
        "skillId": skill_id,
        "params": params,
        "idempotencyKey": f"demo-{skill_id}-{uuid.uuid4().hex[:8]}",
    }
    if paid:
        envelope["payment"] = {
            "provider": "aeon-bnb-x402",
            "amount": "10000",
            "asset": "USDT_OR_USDC_CONTRACT",
            "network": "eip155:56",
        }
        envelope["transaction_details"] = {
            "txHash": f"0x{uuid.uuid4().hex}",
            "verified": True,
        }
    return envelope


def main():
    parser = argparse.ArgumentParser(description="G1 MuJoCo Demo")
    parser.add_argument("--skill", default="move_forward", help="Skill to execute")
    parser.add_argument("--robot-id", default="g1-demo-001")
    parser.add_argument("--zenoh-endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--speed", type=float, default=0.5)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--goal-x", type=float, default=5.0)
    parser.add_argument("--goal-y", type=float, default=3.0)
    parser.add_argument("--no-pay", action="store_true", help="Send without payment (expect 402)")
    args = parser.parse_args()

    # Build params
    if args.skill == "move_forward":
        params = {"durationSec": args.duration, "speed": args.speed}
    elif args.skill == "navigate_obstacle":
        params = {"goal_x": args.goal_x, "goal_y": args.goal_y}
    elif args.skill == "stop":
        params = {}
    else:
        params = {}

    paid = not args.no_pay
    envelope = create_action_envelope(args.robot_id, args.skill, params, paid=paid)

    logger.info("=== Fabric Action Demo ===")
    logger.info("Skill: %s | Paid: %s | Robot: %s", args.skill, paid, args.robot_id)
    logger.info("ActionId: %s", envelope["actionId"])

    if args.no_pay:
        logger.info("Sending UNPAID request (expect 402)...")
    else:
        logger.info("Sending PAID request with txHash: %s", envelope.get("transaction_details", {}).get("txHash", "N/A"))

    # Connect to Zenoh
    conf = zenoh.Config.from_json5(
        f'{{"connect":{{"endpoints":["{args.zenoh_endpoint}"]}}}}'
    )
    session = zenoh.open(conf)

    # Publish action
    payload = json.dumps({"payload": envelope, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")})
    session.put("robot/tunnel/action", payload)
    logger.info("Published to Zenoh: robot/tunnel/action")
    logger.info("Payload: %s", json.dumps(envelope, indent=2)[:200])

    # Wait for result
    result_received = []

    def on_result(sample):
        raw = bytes(sample.payload)
        result = json.loads(raw)
        result_received.append(result)
        logger.info("RESULT: %s", json.dumps(result, indent=2))

    sub = session.declare_subscriber("robot/tunnel/result", on_result)

    # Wait up to 10 seconds for result
    logger.info("Waiting for result on robot/tunnel/result...")
    for i in range(100):
        time.sleep(0.1)
        if result_received:
            break

    if not result_received:
        logger.warning("No result received within 10 seconds")

    sub.undeclare()
    session.close()

    logger.info("=== Demo Complete ===")


if __name__ == "__main__":
    main()
