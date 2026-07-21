"""Entrypoint for the Reachy Mini MuJoCo simulation bridge."""
import logging
import sys
import os

# Setup paths so node.py can find src/ and the package itself
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                           # bridge package root
sys.path.insert(0, os.path.join(_HERE, "src"))      # simulation/policy modules

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

from reachy_mini.node import ReachyMiniBridgeNode


def main():
    print("=" * 70)
    print("  Fabric Foundation RoboPay — Reachy Mini MuJoCo Bridge")
    print("  Zenoh topic  : robot/tunnel/action")
    print("  Metrics topic: robot/reachy_mini/metrics")
    print("=" * 70)
    print()
    print("  Waiting for ActionEvents from the Fabric tunnel...")
    print("  Run test_publisher.py in another terminal to trigger an action.")
    print()

    node = ReachyMiniBridgeNode(zenoh_listen="tcp/127.0.0.1:7447")
    node.spin()


if __name__ == "__main__":
    main()
