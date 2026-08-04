"""
End-to-end demo for the Deep Robotics X30 Pro Tier 1 RoboPay skill.

Simulates the full contract locally without a live Tunnel/relay:
  1. unpaid action -> rejected (no simulator call)
  2. paid action -> X30 Pro MuJoCo episode -> success + settlementEligible
  3. replay of the same actionId -> rejected, no second motion
  4. stop action -> immediate success, no payment, not settlement-eligible

Prints real metrics captured from the MuJoCo X30 Pro scene, and writes them
to docs/evidence/x30_pro_metrics.json for the validation report.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bridge.x30_pro_zenoh_bridge import (
    X30ProRoboPayBridge,
    ReplayStore,
    canonical_params_hash,
)
from simulation.runners.x30_pro_runner import X30ProMuJoCoRunner

ROBOT_ID = "deep-robotics-x30-pro-sim-01"
SKILL_ID = "x30_pro_obstacle_navigation"


def make_action(action_id: str, idem_key: str, params: dict, verified: bool = True):
    return {
        "actionId": action_id,
        "robotId": ROBOT_ID,
        "skillId": SKILL_ID,
        "params": params,
        "paramsHash": canonical_params_hash(params),
        "idempotencyKey": idem_key,
        "payment": {
            "status": "verified" if verified else "unverified",
            "verified": verified,
            "authorizationId": f"auth-{action_id}",
            "expiresAt": time.time() + 300,
        },
    }


def main():
    scene_path = os.path.join(
        os.path.dirname(__file__), "..", "simulation", "scenes", "x30_pro.xml"
    )
    runner = X30ProMuJoCoRunner(scene_path=scene_path)
    bridge = X30ProRoboPayBridge(robot_id=ROBOT_ID, runner=runner, replay_store=ReplayStore())

    params = {"target_xy": [7.0, 0.0], "max_episode_steps": 20000}

    print("=== Step 1: unpaid request ===")
    unpaid = make_action("demo-action-001", "demo-idem-001", params, verified=False)
    r1 = bridge.handle_raw_action(unpaid)
    print(r1.to_json())
    assert r1.status == "rejected" and r1.settlementEligible is False

    print("\n=== Step 2: paid request -> real MuJoCo X30 Pro episode ===")
    paid = make_action("demo-action-002", "demo-idem-002", params, verified=True)
    t0 = time.time()
    r2 = bridge.handle_raw_action(paid)
    wall_time = time.time() - t0
    print(r2.to_json())
    print(f"[wall clock] episode took {wall_time:.2f}s")

    print("\n=== Step 3: replay same actionId -> must reject, no second motion ===")
    replay = make_action("demo-action-002", "demo-idem-002", params, verified=True)
    r3 = bridge.handle_raw_action(replay)
    print(r3.to_json())
    assert r3.status == "rejected" and r3.settlementEligible is False

    print("\n=== Step 4: stop action -> no payment required ===")
    r4 = bridge.handle_stop("demo-action-003")
    print(r4.to_json())
    assert r4.status == "success" and r4.settlementEligible is False

    results_dir = os.path.join(os.path.dirname(__file__), "..", "docs", "evidence")
    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, "x30_pro_metrics.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                "unpaid_rejected": json.loads(r1.to_json()),
                "paid_success": json.loads(r2.to_json()),
                "replay_rejected": json.loads(r3.to_json()),
                "stop": json.loads(r4.to_json()),
                "wall_clock_episode_seconds": round(wall_time, 3),
            },
            f,
            indent=2,
        )
    print(f"\nMetrics written to {out_path}")


if __name__ == "__main__":
    main()
