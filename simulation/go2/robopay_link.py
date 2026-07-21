"""RoboPay -> simulation link: execute paid robot actions in MuJoCo.

Subscribes to the Zenoh topic the RoboPay tunnel publishes paid actions to
(`robot/tunnel/action`, see tunnel/internal/handlers/handlers.go). Every
event triggers a navigation episode; the resulting physics metrics are
logged and written next to this file.

Both payment paths in the tunnel (x402 `POST /action` and the AIP agent)
publish to this same topic, so subscribing here covers either payment rail.

Usage: python3 robopay_link.py [--once]
  --once: exit after the first executed action (used by the e2e test)
"""

import argparse
import json
import pathlib
import sys
import time

import zenoh

from go2_nav import run_episode

ACTION_TOPIC = "robot/tunnel/action"
RESULT_FILE = pathlib.Path(__file__).parent / "last_action_result.json"

# Task used when the paid action carries no explicit navigation task
DEFAULT_TASK = {
    "obstacles": [["box", 2.5, 0, 0.5, 0.5, 0.5],
                  ["box", 5.0, 1.5, 0.4, 0.4, 0.6],
                  ["cylinder", 7.0, -1.0, 0.3, 0.5]],
    "goal": [10.0, 0.0],
}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def execute_action(event):
    """Run the navigation task described by a paid-action event."""
    payload = event.get("payload") or {}
    task = payload.get("task") or DEFAULT_TASK
    obstacles = [tuple(ob) for ob in task["obstacles"]]
    goal = tuple(task["goal"])
    tx = event.get("transaction_details") or {}

    log(f"action received: goal={goal}, {len(obstacles)} obstacles, "
        f"payment={json.dumps(tx.get('payment_payload'))[:80]}")
    t0 = time.time()
    metrics = run_episode(obstacles, goal)
    metrics.pop("trajectory")
    wall = round(time.time() - t0, 2)
    result = {"event_timestamp": event.get("timestamp"),
              "wall_time_s": wall, "metrics": metrics}
    RESULT_FILE.write_text(json.dumps(result, indent=1))
    log(f"episode done in {wall}s wall: reached={metrics['reached']} "
        f"collisions={metrics['collisions']} -> {RESULT_FILE.name}")
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    session = zenoh.open(zenoh.Config())
    done = []

    def on_sample(sample):
        try:
            event = json.loads(bytes(sample.payload))
        except ValueError:
            log(f"ignoring non-JSON payload on {ACTION_TOPIC}")
            return
        metrics = execute_action(event)
        done.append(metrics)

    session.declare_subscriber(ACTION_TOPIC, on_sample)
    log(f"listening on zenoh topic '{ACTION_TOPIC}'")
    try:
        while not (args.once and done):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    if args.once:
        sys.exit(0 if done and done[0]["reached"] else 1)


if __name__ == "__main__":
    main()
