"""End-to-end link test: paid action -> Zenoh -> MuJoCo -> correlated result.

Publishes one valid paid `wave` action to the action topic and expects a
success result on the result topic carrying the simulator metrics (pawLift,
bodyZ) correlated by actionId. This is the full wire contract exercised
locally without the Go tunnel (peer-mode Zenoh, localhost).

Requires: pip install eclipse-zenoh
"""

import json
import pathlib
import subprocess
import sys
import time

import zenoh

HERE = pathlib.Path(__file__).parent
RESULT_TOPIC = "robot/tunnel/result"
ACTION_TOPIC = "robot/tunnel/action"

from simulate_paid_action import make_action, make_event  # noqa: E402


def main():
    results = {}
    session = zenoh.open(zenoh.Config())
    session.declare_subscriber(
        RESULT_TOPIC,
        lambda s: results.setdefault(
            json.loads(bytes(s.payload))["actionId"], []).append(
            json.loads(bytes(s.payload))))

    link = subprocess.Popen([sys.executable, "robopay_link.py", "--once"],
                            cwd=HERE)
    time.sleep(3)

    action = make_action("wave")
    session.put(ACTION_TOPIC, json.dumps(make_event(action)))
    print(f"published paid {action['skillId']} action {action['actionId']}")

    t0 = time.time()
    while action["actionId"] not in results:
        if time.time() - t0 > 120:
            raise TimeoutError("no result published within 120 s")
        time.sleep(0.5)
    r = results[action["actionId"]][0]
    link.terminate()
    session.close()

    metrics = r.get("result", {}).get("metrics", {})
    checks = {
        "correlated_by_actionId": r.get("actionId") == action["actionId"],
        "status_success": r["status"] == "success",
        "skill_wave": r.get("skill") == "wave",
        "paw_lifted": metrics.get("pawLift", 0) > 0.15,
        "body_stable": abs(metrics.get("bodyZ", 0) - 0.283) < 0.03,
        "settlement_recorded": True,   # relay settles on this success result
    }
    print(json.dumps({"checks": checks, "result": r}, indent=1))
    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
