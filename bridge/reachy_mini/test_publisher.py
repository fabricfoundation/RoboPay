"""Test publisher — simulates the Fabric tunnel sending an ActionEvent via Zenoh.

Usage:
    python test_publisher.py --action pick_and_place
    python test_publisher.py --action door_open --duration 6.0

This script mimics what the tunnel/Go runtime does after verifying x402 payment:
it publishes the ActionEvent JSON payload to the Zenoh topic robot/tunnel/action.
"""
import argparse
import json
import time
from datetime import datetime, timezone

import zenoh


ZENOH_TOPIC_ACTION  = "robot/tunnel/action"
ZENOH_TOPIC_METRICS = "robot/reachy_mini/metrics"


def build_action_event(action: str, duration: float) -> bytes:
    """Build an ActionEvent payload identical to what the tunnel/Go produces."""
    event = {
        "payload": {
            "action": action,
            "params": {
                "duration": duration,
                "target":   "red_cube",
                "destination": "goal_zone",
            },
        },
        "transaction_details": {
            "tx_hash":      "0xa1b2c3d4e5f67890123456789abcdef0123456789abcdef0123456789abcdef0",
            "payer":        "0x71C7656EC7ab88b098defB751B7401B5f6d8976F",
            "payee":        "0x3C44CdD45919C5E40ed375751978d461D4C09088",
            "amount_robo":  14000.0,
            "network":      "eip155:84532",
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(event).encode()


def on_metrics(sample):
    """Callback to print metrics published by the bridge."""
    raw = bytes(sample.payload.to_bytes())
    try:
        data = json.loads(raw)
        print("\n" + "=" * 70)
        print("  METRICS received from bridge:")
        print("=" * 70)
        print(json.dumps(data, indent=2))
        print("=" * 70 + "\n")
    except Exception as e:
        print(f"Failed to parse metrics: {e}")


def main():
    parser = argparse.ArgumentParser(description="Fabric Tunnel Simulator (test publisher)")
    parser.add_argument("--action",   default="pick_and_place",
                        help="Action to send (pick_and_place, door_open, wave, stop)")
    parser.add_argument("--duration", type=float, default=8.0,
                        help="Simulation duration in seconds")
    parser.add_argument("--connect",  default="tcp/127.0.0.1:7447",
                        help="Zenoh endpoint to connect to (must match bridge listen address)")
    args = parser.parse_args()

    print("=" * 70)
    print("  Fabric Tunnel Simulator (test_publisher)")
    print(f"  Action  : {args.action}")
    print(f"  Duration: {args.duration}s")
    print(f"  Zenoh   : {args.connect}")
    print("=" * 70)

    # Open Zenoh session (connecting to the bridge's listener)
    conf = zenoh.Config.from_json5(
        f'{{"connect":{{"endpoints":["{args.connect}"]}}}}'
    )
    session = zenoh.open(conf)

    # Subscribe to metrics topic to print bridge response
    sub = session.declare_subscriber(ZENOH_TOPIC_METRICS, on_metrics)

    # Small delay so bridge subscriber is ready
    time.sleep(0.5)

    payload = build_action_event(args.action, args.duration)
    print(f"\nPublishing ActionEvent to '{ZENOH_TOPIC_ACTION}'...")
    session.put(ZENOH_TOPIC_ACTION, payload)
    print("Published! Waiting for bridge to finish simulation and return metrics...\n")

    # Wait enough time for simulation + Sim2Sim to finish
    time.sleep(args.duration + 25.0)

    sub.undeclare()
    session.close()
    print("Done.")


if __name__ == "__main__":
    main()
