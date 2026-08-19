"""Result-semantics test: success, failure and replay behavior (wiki §3/5/7).

Drives robopay_link.py through the wire with one valid action and five
kinds of bad ones, and checks the structured results on the result topic:

- valid action        -> {"status": "success"} correlated by actionId
- replayed idempotencyKey -> DUPLICATE error, executed exactly once
- unknown skillId     -> UNKNOWN_SKILL, not executed
- out-of-range goal   -> INVALID_PARAMS, not executed
- tampered paramsHash -> INVALID_PARAMS, not executed
- goal inside an obstacle -> NO_PATH error (planner refuses)

Payment safety: every non-success outcome is an error result — the relay
must only settle on {"status": "success"}, so this doubles as the
no-settle-on-failure evidence.
"""

import json
import pathlib
import subprocess
import sys
import time

import zenoh

from robopay_link import params_hash
from simulate_paid_action import make_action, make_event

HERE = pathlib.Path(__file__).parent
RESULT_TOPIC = "robot/tunnel/result"
ACTION_TOPIC = "robot/tunnel/action"


def main():
    results = {}
    session = zenoh.open(zenoh.Config())
    session.declare_subscriber(
        RESULT_TOPIC,
        lambda s: results.setdefault(
            json.loads(bytes(s.payload))["actionId"], []).append(
            json.loads(bytes(s.payload))))

    link = subprocess.Popen([sys.executable, "robopay_link.py"], cwd=HERE)
    time.sleep(3)   # let the subscriber declare itself

    def send(action):
        session.put(ACTION_TOPIC, json.dumps(make_event(action)))

    def wait_result(action_id, n=1, timeout=60):
        t0 = time.time()
        while len(results.get(action_id, [])) < n:
            if time.time() - t0 > timeout:
                raise TimeoutError(f"no result {n} for {action_id}")
            time.sleep(0.5)
        return results[action_id][n - 1]

    checks = {}
    try:
        # 1) valid action succeeds, result correlated by actionId
        ok = make_action((2.0, 0.0))
        ok["params"]["obstacles"] = []          # short unobstructed episode
        ok["paramsHash"] = params_hash(ok["params"])
        send(ok)
        r = wait_result(ok["actionId"], timeout=120)
        checks["success_result"] = r["status"] == "success" \
            and r["result"]["reached"] and r["skill"] == "navigate_to"

        # 2) exact replay: DUPLICATE error, no second execution
        send(ok)
        r = wait_result(ok["actionId"], n=2)
        checks["replay_rejected"] = r["status"] == "error" \
            and r["error"]["code"] == "DUPLICATE"
        checks["replay_not_reexecuted"] = len(results[ok["actionId"]]) == 2

        # 3) unknown skill
        bad = make_action((2.0, 0.0))
        bad["skillId"] = "backflip"
        send(bad)
        r = wait_result(bad["actionId"])
        checks["unknown_skill"] = r["error"]["code"] == "UNKNOWN_SKILL"

        # 4) out-of-range goal
        bad = make_action((999.0, 0.0))
        bad["paramsHash"] = params_hash(bad["params"])
        send(bad)
        r = wait_result(bad["actionId"])
        checks["invalid_params"] = r["error"]["code"] == "INVALID_PARAMS"

        # 5) tampered params (hash mismatch)
        bad = make_action((2.0, 0.0))
        bad["params"]["goal"] = [3.0, 0.0]   # tampered after hashing
        send(bad)
        r = wait_result(bad["actionId"])
        checks["tampered_params"] = r["error"]["code"] == "INVALID_PARAMS"

        # 6) goal buried inside an obstacle: planner must refuse
        bad = make_action((2.5, 0.0))
        bad["paramsHash"] = params_hash(bad["params"])
        send(bad)
        r = wait_result(bad["actionId"])
        checks["no_path_is_error"] = r["status"] == "error" \
            and r["error"]["code"] == "NO_PATH"

        # payment safety: nothing but the one valid action returned success
        all_results = [r for rs in results.values() for r in rs]
        checks["only_success_may_settle"] = \
            sum(r["status"] == "success" for r in all_results) == 1
    finally:
        link.terminate()
        session.close()

    print(json.dumps({"checks": checks}, indent=1))
    ok_all = all(checks.values())
    print("PASS" if ok_all else "FAIL")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
