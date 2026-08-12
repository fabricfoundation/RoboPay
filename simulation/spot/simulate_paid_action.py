"""Simulate a settled paid action on the RoboPay wire.

Publishes to the action topic the exact event the tunnel's PostAction
handler emits after the x402 payment middleware clears a payment
(tunnel/internal/handlers/handlers.go): our action envelope as `payload`,
plus `transaction_details` and `timestamp`. Simulation-only stand-in for
the payment settlement itself; topic and schema match the real tunnel.

The action envelope carries actionId, robotId, skillId, params,
paramsHash, idempotencyKey and payment (with a simulated receipt/txHash),
all preserved end-to-end and validated by robopay_link.py.

Usage:
  python3 simulate_paid_action.py                 # sit
  python3 simulate_paid_action.py wave
  python3 simulate_paid_action.py turn_to_face 30
"""

import json
import os
import sys
import time
import uuid

import zenoh

from robopay_link import ROBOT_ID, params_hash
from payment_gate import PaymentGate

ACTION_TOPIC = os.environ.get("ROBOPAY_ACTION_TOPIC", "robot/tunnel/action")
ROBOT_ID_SIM = ROBOT_ID

_GATE = PaymentGate()   # shares the facilitator key with the robot link


def make_action(skill_id, params=None, action_id=None, idempotency_key=None,
                payment=None):
    """The body a payer POSTs to /action for a given skill.

    By default the payment is a valid, facilitator-signed receipt for this
    exact action (issue_receipt signs actionId/skillId/paramsHash), so the
    robot link's x402 gate verifies it end to end. Pass ``payment`` to
    override (used by the tests to send unpaid/forged receipts).
    """
    params = params or {}
    action_id = action_id or f"act_{uuid.uuid4().hex[:12]}"
    if payment is None:
        payment = _GATE.facilitator.issue_receipt(
            action_id, skill_id, params)
        payment["scheme"] = "exact"
        payment["amountUSDC"] = "0.002"
        payment["simulated"] = True
    return {
        "actionId": action_id,
        "robotId": ROBOT_ID_SIM,
        "skillId": skill_id,
        "params": params,
        "paramsHash": params_hash(params),
        "idempotencyKey": idempotency_key or f"idem-{action_id}",
        "payment": payment,
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
    skill = sys.argv[1] if len(sys.argv) > 1 else "sit"
    params = {}
    if skill == "turn_to_face" and len(sys.argv) > 2:
        params["headingDeg"] = float(sys.argv[2])
    if skill == "hold" and len(sys.argv) > 2:
        params["seconds"] = float(sys.argv[2])
    action = make_action(skill, params)
    publish(make_event(action))
    print(f"published paid action {action['actionId']} to '{ACTION_TOPIC}': "
          f"skill={skill} params={params}")


if __name__ == "__main__":
    main()
