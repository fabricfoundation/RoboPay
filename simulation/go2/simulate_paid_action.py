"""Simulate a settled paid action on the RoboPay wire.

Publishes to `robot/tunnel/action` the exact event the tunnel's PostAction
handler emits after the x402 payment middleware clears a payment
(tunnel/internal/handlers/handlers.go). Simulation-only stand-in for the
payment settlement itself; topic and schema match the real tunnel.

Usage: python3 simulate_paid_action.py [goal_x goal_y]
"""

import json
import sys
import time

import zenoh

ACTION_TOPIC = "robot/tunnel/action"


def make_event(goal):
    # Mirrors handlers.PostAction: payload + transaction_details + timestamp.
    return {
        "payload": {
            "task": {
                "obstacles": [["box", 2.5, 0, 0.5, 0.5, 0.5],
                              ["box", 5.0, 1.5, 0.4, 0.4, 0.6],
                              ["cylinder", 7.0, -1.0, 0.3, 0.5]],
                "goal": list(goal),
            },
        },
        "transaction_details": {
            "payment_payload": {
                "scheme": "exact",
                "network": "eip155:84532",
                "payer": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
                "simulated": True,
            },
            "payment_requirements": {
                "scheme": "exact",
                "price": "$0.002",
                "payTo": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
            },
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def main():
    goal = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) == 3 \
        else (10.0, 0.0)
    session = zenoh.open(zenoh.Config())
    event = make_event(goal)
    session.put(ACTION_TOPIC, json.dumps(event))
    print(f"published paid action to '{ACTION_TOPIC}': goal={goal}")
    time.sleep(0.5)   # let zenoh flush before closing
    session.close()


if __name__ == "__main__":
    main()
