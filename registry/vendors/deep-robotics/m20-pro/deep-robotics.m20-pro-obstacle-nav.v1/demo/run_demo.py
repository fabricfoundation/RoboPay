"""
End-to-end demo for the Deep Robotics M20 Pro Tier 1 RoboPay bridge.

Payment verification and settlement now happen entirely in the Go
tunnel before an event ever reaches robot/tunnel/action -- see
tunnel/pay_m20_pro.py and docs/evidence/base-sepolia/live-payment-e2e.md
for the full paid flow against a real facilitator and real Base Sepolia
settlement.

This script demonstrates what's left at the bridge layer once an event
has already passed the tunnel's fail-closed gate:
  1. a well-formed, allowlisted action -> real MuJoCo M20 Pro episode -> success
  2. replay of the same actionId -> rejected, no second motion
  3. stop -> immediate success

Prints real metrics captured from the MuJoCo M20 Pro scene, and writes
them to docs/evidence/m20_pro_metrics.json for the validation report.
"""

import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bridge.m20_pro_zenoh_bridge import M20ProBridge, SKILL_ID  # noqa: E402


def make_sample(action_id: str, params: dict):
    event = {
        "actionId": action_id,
        "action": SKILL_ID,
        "params": params,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return SimpleNamespace(payload=json.dumps(event).encode("utf-8"))


def main():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    bridge = M20ProBridge(db_path=db_path)
    published = []
    bridge._publish = lambda result: published.append(result)

    params = {"target_xy": [8.0, 0.0], "max_episode_steps": 50000}

    print("=== Step 1: well-formed action -> real MuJoCo M20 Pro episode ===")
    t0 = time.time()
    bridge._on_action(make_sample("demo-action-001", params))
    wall_time = time.time() - t0
    r1 = published[-1]
    print(json.dumps(r1))
    print(f"[wall clock] episode took {wall_time:.2f}s")
    assert r1["status"] == "success"

    print("\n=== Step 2: replay same actionId -> must reject, no second motion ===")
    bridge._on_action(make_sample("demo-action-001", params))
    r2 = published[-1]
    print(json.dumps(r2))
    assert r2["status"] == "rejected" and r2["errorCode"] == "replay_detected"

    print("\n=== Step 3: stop action -> immediate success ===")
    r3 = bridge.handle_stop("demo-action-002")
    print(json.dumps(r3))
    assert r3["status"] == "success"

    results_dir = os.path.join(os.path.dirname(__file__), "..", "docs", "evidence")
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, "m20_pro_metrics.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                "paid_success": r1,
                "replay_rejected": r2,
                "stop": r3,
                "wall_clock_episode_seconds": round(wall_time, 3),
            },
            f,
            indent=2,
        )
    print(f"\nMetrics written to {out_path}")

    bridge.guard.close()
    os.remove(db_path)


if __name__ == "__main__":
    main()
