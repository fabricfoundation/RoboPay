import json
import time
import zenoh


def main():
    print("Opening Zenoh session...")
    session = zenoh.open(zenoh.Config())

    # Handler for receiving telemetry back from the bridge
    def on_result(sample):
        print("\n================ [RECEIVED BRIDGE RESPONSE] ================")
        payload = sample.payload.to_string()
        try:
            formatted_json = json.dumps(json.loads(payload), indent=2)
            print(formatted_json)
        except Exception:
            print(payload)
        print("============================================================\n")

    # 1. Subscribe to result channel
    print("Subscribing to 'robopay/action/result'...")
    _sub = session.declare_subscriber("robopay/action/result", on_result)

    # 2. Publisher for action requests
    pub = session.declare_publisher("robopay/action/request")

    # Brief delay to ensure subscriptions propagate across the Zenoh session
    time.sleep(1)

    # 3. Payload with valid payment proof
    test_payload = {
        "actionId": "test-walk-001",
        "action": "walk",
        "skill_id": "walk",
        "payment_proof": "proof-ok",
    }

    print(f"Publishing paid action request to 'robopay/action/request': {test_payload['action']}")
    pub.put(json.dumps(test_payload))

    # Hold session open to catch the incoming response
    print("Waiting for bridge result metrics...")
    time.sleep(3)

    session.close()
    print("Test complete.")


if __name__ == "__main__":
    main()