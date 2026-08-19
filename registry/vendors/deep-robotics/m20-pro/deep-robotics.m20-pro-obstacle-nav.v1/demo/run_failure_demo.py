"""
Deliberate-failure demo: proves that a timeout episode produces
status=error at the bridge layer, so the tunnel's ExecutionWatcher
never settles it -- satisfying the reviewer requirement that
failure/timeout must never settle payment.
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

    print("=== Deliberate failure: max_episode_steps too small to reach goal ===")
    params = {"target_xy": [8.0, 0.0], "max_episode_steps": 50}
    bridge._on_action(make_sample("demo-failure-001", params))
    result = published[-1]
    print(json.dumps(result))

    assert result["status"] == "error"
    assert result["simulatorStatus"] == "timeout"
    print("\nPASS: timeout episode correctly produced status=error (never settled).")

    out_dir = os.path.join(os.path.dirname(__file__), "..", "docs", "evidence")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "m20_pro_failure_metrics.json"), "w") as f:
        json.dump(result, f, indent=2)

    bridge.guard.close()
    os.remove(db_path)


if __name__ == "__main__":
    main()
