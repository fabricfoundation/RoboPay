"""Simulate a settled paid action on the RoboPay wire.

Publishes to the action topic the exact event the tunnel's PostAction
handler emits after the x402 payment middleware clears a payment
(tunnel/internal/handlers/handlers.go): our action envelope as `payload`,
plus `transaction_details` and `timestamp`. Simulation-only stand-in for
the payment settlement itself; topic and schema match the real tunnel.

The action envelope carries actionId, robotId, skillId, params,
paramsHash, idempotencyKey and payment (with a simulated receipt/txHash),
all preserved end-to-end and validated by robopay_link.py.

Usage: python3 simulate_paid_action.py [goal_x goal_y]
"""

import json
import os
import sys
import time
import uuid

import zenoh

from robopay_link import ROBOT_ID, params_hash

ACTION_TOPIC = os.environ.get("ROBOPAY_ACTION_TOPIC", "robot/tunnel/action")


def make_action(goal, action_id=None, idempotency_key=None):
    """The body a payer POSTs to /action for the navigate_to skill."""
    params = {
        "goal": list(goal),
        "obstacles": [["box", 2.5, 0, 0.5, 0.5, 0.5],
                      ["box", 5.0, 1.5, 0.4, 0.4, 0.6],
                      ["cylinder", 7.0, -1.0, 0.3, 0.5]],
    }
    action_id = action_id or f"act_{uuid.uuid4().hex[:12]}"
    return {
        "actionId": action_id,
        "robotId": ROBOT_ID,
        "skillId": "navigate_to",
        "params": params,
        "paramsHash": params_hash(params),
        "idempotencyKey": idempotency_key or f"idem-{action_id}",
        "payment": {
            "scheme": "exact",
            "network": "eip155:84532",
            "payer": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
            "amountUSDC": "0.002",
            "txHash": "0x" + "ab" * 32,   # simulated settlement receipt
            "simulated": True,
        },
    }


def make_event(action):
    # Mirrors handlers.PostAction: payload + transaction_details + timestamp.
    return {
        "payload": action,
        "transaction_details": {
            "payment_payload": action["payment"],
            "payment_requirements": {
                "scheme": "exact",
                "price": "$0.002",
                "payTo": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            },
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def publish(event):
    session = zenoh.open(zenoh.Config())
    session.put(ACTION_TOPIC, json.dumps(event))
    time.sleep(0.5)   # let zenoh flush before closing
    session.close()


def main():
    goal = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) == 3 \
        else (10.0, 0.0)
    action = make_action(goal)
    publish(make_event(action))
    print(f"published paid action {action['actionId']} to '{ACTION_TOPIC}': "
          f"goal={goal}")


if __name__ == "__main__":
    main()
