"""Manual E2E demo: publishes one action envelope to robot/tunnel/action
and prints the correlated robot/tunnel/result. Stands in for the Tunnel
component (which would normally publish after x402 verification) so the
bridge's behavior can be demonstrated without the full Fabric/Tunnel stack.

Usage:
    python3 send_test_action.py                      # valid action, expect success
    python3 send_test_action.py --unpaid              # expect status=rejected
    python3 send_test_action.py --replay-of ACTIONID  # resend an actionId, expect rejected
"""
import argparse
import hashlib
import json
import sys
import time
import uuid

import zenoh

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"


def canonical_params_hash(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_envelope(action_id, idempotency_key, unpaid, auth_id):
    params = {"goal_x": 5.0, "goal_y": 0.0, "max_time_sec": 60}
    return {
        "actionId": action_id,
        "robotId": "booster-k1-sim-01",
        "skillId": "k1_navigate_avoid_obstacles",
        "params": params,
        "paramsHash": canonical_params_hash(params),
        "idempotencyKey": idempotency_key,
        "payment": {
            "provider": "x402",
            "authorizationId": auth_id,
            "verified": not unpaid,
            "status": "pending" if unpaid else "authorized",
            "settled": False,
            "network": "eip155:84532",
            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            "amount": "1000",
            "payTo": "0xRobotPayeeAddress",
            "issuedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 300)),
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unpaid", action="store_true", help="Send with an unverified/pending payment")
    parser.add_argument("--replay-of", default=None, help="Reuse this actionId to test replay rejection")
    parser.add_argument("--timeout", type=float, default=90.0, help="Seconds to wait for a correlated result")
    args = parser.parse_args()

    if args.replay_of:
        action_id = args.replay_of
        idempotency_key = f"idem_{action_id}"
        auth_id = f"auth_{action_id}"
    else:
        action_id = f"act_{uuid.uuid4().hex[:16]}"
        idempotency_key = f"idem_{uuid.uuid4().hex[:16]}"
        auth_id = f"auth_{uuid.uuid4().hex[:16]}"

    envelope = build_envelope(action_id, idempotency_key, args.unpaid, auth_id)

    session = zenoh.open(zenoh.Config())
    received = {}

    def on_result(sample):
        result = json.loads(bytes(sample.payload))
        if result.get("actionId") == action_id:
            received["result"] = result

    session.declare_subscriber(RESULT_TOPIC, on_result)
    publisher = session.declare_publisher(ACTION_TOPIC)

    # Give Zenoh peer discovery/routing a moment to establish before the
    # first publish, otherwise a publish immediately after session open
    # can be dropped before the bridge's subscriber route exists.
    time.sleep(5)

    print(f"[client] Publishing actionId={action_id} idempotencyKey={idempotency_key} unpaid={args.unpaid}")
    publisher.put(json.dumps(envelope).encode("utf-8"))

    start = time.time()
    while "result" not in received and (time.time() - start) < args.timeout:
        time.sleep(0.2)

    session.close()

    if "result" not in received:
        print(f"[client] TIMEOUT: no result received on {RESULT_TOPIC} within {args.timeout}s")
        sys.exit(2)

    print(f"[client] Received result for actionId={action_id}:")
    print(json.dumps(received["result"], indent=2))

    if received["result"]["status"] == "success":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
