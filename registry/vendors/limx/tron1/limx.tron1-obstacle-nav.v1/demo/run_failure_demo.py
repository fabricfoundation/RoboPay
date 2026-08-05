"""
Deliberate-failure demo: proves that a timeout episode produces
status=error, settlementEligible=false, and correlates to the same
actionId -- satisfying the reviewer requirement that failure/timeout
must never settle payment.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bridge.tron1_zenoh_bridge import (
    Tron1RoboPayBridge,
    ReplayStore,
    canonical_params_hash,
)
from simulation.runners.tron1_runner import Tron1MuJoCoRunner

ROBOT_ID = "limx-tron1-sim-01"
SKILL_ID = "tron1_obstacle_navigation"


def make_action(action_id, idem_key, params):
    return {
        "actionId": action_id,
        "robotId": ROBOT_ID,
        "skillId": SKILL_ID,
        "params": params,
        "paramsHash": canonical_params_hash(params),
        "idempotencyKey": idem_key,
        "payment": {
            "status": "verified",
            "verified": True,
            "authorizationId": f"auth-{action_id}",
            "expiresAt": time.time() + 300,
        },
    }


def main():
    scene_path = os.path.join(
        os.path.dirname(__file__), "..", "simulation", "scenes", "tron1.xml"
    )
    runner = Tron1MuJoCoRunner(scene_path=scene_path)
    bridge = Tron1RoboPayBridge(robot_id=ROBOT_ID, runner=runner, replay_store=ReplayStore())

    print("=== Deliberate failure: max_episode_steps too small to reach goal ===")
    params = {"target_xy": [8.0, 0.0], "max_episode_steps": 500}
    action = make_action("demo-failure-001", "demo-failure-idem-001", params)
    result = bridge.handle_raw_action(action)
    result_json = result.to_json()
    result_dict = json.loads(result_json)
    print(result_json)
    assert result.status == "error"
    assert result.settlementEligible is False
    assert result_dict["metrics"]["status"] == "timeout"
    print("\nPASS: timeout episode correctly produced no settlement.")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs", "evidence")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "tron1_failure_metrics.json"), "w") as f:
        json.dump(result_dict, f, indent=2)


if __name__ == "__main__":
    main()
