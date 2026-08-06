"""Entrypoint for the Reachy Mini MuJoCo simulation bridge."""
import logging
import sys
import os

# Setup paths so node.py can find the bridge package root
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

from reachy_mini.node import BridgeSettings, ReachyMiniBridgeNode


def main():
    settings = BridgeSettings.from_env()
    print("=" * 70)
    print("  Fabric Foundation RoboPay — Reachy Mini MuJoCo Bridge")
    print(f"  Robot ID      : {settings.robot_id}")
    print(f"  Zenoh endpoint: {settings.zenoh_endpoint}")
    print(f"  Action topic  : {settings.action_topic}")
    print(f"  Result topic  : {settings.result_topic}")
    print(f"  Metrics topic : {settings.metrics_topic}")
    print("=" * 70)
    print()
    print("  Waiting for ActionEvents from the Fabric tunnel...")
    print("  Run test_e2e_paid_action.py (or a paid Tunnel client) in another terminal.")
    print()

    node = ReachyMiniBridgeNode(settings=settings)
    node.spin()


if __name__ == "__main__":
    main()
