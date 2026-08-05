"""Local live Zenoh evidence client (the payment object is explicitly mocked)."""
import json
import threading
import time
import uuid

import zenoh


def main() -> None:
    action_id = str(uuid.uuid4())
    replay_key = str(uuid.uuid4())
    completed = threading.Event()
    received = {}
    config = zenoh.Config.from_json5(
        '{"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}'
    )
    session = zenoh.open(config)

    def on_result(sample):
        value = json.loads(bytes(sample.payload.to_bytes()))
        if value.get("actionId") == action_id:
            received.update(value)
            completed.set()

    subscriber = session.declare_subscriber("robot/tunnel/result", on_result)
    try:
        time.sleep(0.5)
        event = {
        "payload": {
            "action": "move_forward",
            "actionId": action_id,
            "idempotencyKey": replay_key,
            "robotId": "agibot-x2-sim-001",
            "params": {"duration": 0.5, "distance": 0.2},
        },
        "transaction_details": {
            "payment_payload": {"testOnly": True},
            "payment_requirements": {"asset": "ROBO"},
        },
        "timestamp": "2026-08-05T18:00:00Z",
        }
        session.put("robot/tunnel/action", json.dumps(event))
        if not completed.wait(10):
            raise TimeoutError("no correlated terminal result")
        print(json.dumps(received, indent=2, sort_keys=True))
        assert received["status"] == "SUCCESS"
        assert received["metrics"]["root_displacement"] > 0.01
    finally:
        subscriber.undeclare()
        session.close()


if __name__ == "__main__":
    main()
