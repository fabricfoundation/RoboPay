"""RoboPay -> simulation link: execute paid robot actions in MuJoCo.

Subscribes to the Zenoh topic the RoboPay tunnel publishes paid actions to
(`handlers.go`, both the x402 and AIP rails publish there), validates the
action envelope against the skill catalog, runs the navigation episode, and
publishes a structured result correlated by actionId on the result topic.

Wire contract (documented in ../README.md):
  action topic  ROBOPAY_ACTION_TOPIC  default robot/tunnel/action
  result topic  ROBOPAY_RESULT_TOPIC  default robot/tunnel/result
  robot id      ROBOPAY_ROBOT_ID      default test-robot (tunnel config.json)

Success result: {"status": "success", "actionId", "skill", "result": {...}}
Error result:   {"status": "error", "actionId", "skill",
                 "error": {"code", "message"}}
Error codes: UNKNOWN_SKILL, INVALID_PARAMS, WRONG_ROBOT, NO_PATH,
             ACTION_FAILED, DUPLICATE. A replayed idempotencyKey is never
             re-executed; the relay must not settle on any error result.

Usage: python3 robopay_link.py [--once]
  --once: exit after the first successful action (used by the e2e test)
"""

import argparse
import hashlib
import json
import os
import pathlib
import time

import zenoh

from go2_nav import run_episode

ACTION_TOPIC = os.environ.get("ROBOPAY_ACTION_TOPIC", "robot/tunnel/action")
RESULT_TOPIC = os.environ.get("ROBOPAY_RESULT_TOPIC", "robot/tunnel/result")
ROBOT_ID = os.environ.get("ROBOPAY_ROBOT_ID", "test-robot")

SKILLS_FILE = pathlib.Path(__file__).parent / "skills.json"
RESULT_FILE = pathlib.Path(__file__).parent / "last_action_result.json"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def params_hash(params):
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_catalog():
    catalog = {s["skillId"]: s for s in json.loads(SKILLS_FILE.read_text())}
    log(f"skill catalog for robot '{ROBOT_ID}': "
        + ", ".join(f"{s['skillId']} (${s['priceUSDC']})"
                    for s in catalog.values()))
    return catalog


def validate(action, catalog):
    """Returns (error_code, message), or None if the action is executable."""
    robot = action.get("robotId")
    if robot is not None and robot != ROBOT_ID:
        return "WRONG_ROBOT", f"action addressed to {robot!r}, I am {ROBOT_ID!r}"
    skill = catalog.get(action.get("skillId"))
    if skill is None:
        return "UNKNOWN_SKILL", f"unknown skillId {action.get('skillId')!r}"
    params = action.get("params") or {}
    declared = action.get("paramsHash")
    if declared is not None and declared != params_hash(params):
        return "INVALID_PARAMS", "paramsHash does not match params"
    schema = skill["paramsSchema"]
    for name in schema:
        if name not in params:
            return "INVALID_PARAMS", f"missing required param {name!r}"
    for name, value in params.items():
        spec = schema.get(name)
        if spec is None:
            return "INVALID_PARAMS", f"unexpected param {name!r}"
        if spec["type"] == "point" and not (
                isinstance(value, list) and len(value) == 2
                and all(isinstance(v, (int, float)) for v in value)
                and all(abs(v) <= spec["absMax"] for v in value)):
            return "INVALID_PARAMS", \
                f"{name!r} must be [x, y] with |coord| <= {spec['absMax']}"
        if spec["type"] == "obstacles" and not (
                isinstance(value, list) and len(value) <= spec["maxCount"]):
            return "INVALID_PARAMS", \
                f"{name!r} must be a list of at most {spec['maxCount']} obstacles"
    return None


def execute(action):
    """Runs the validated navigation skill. Returns the metrics dict."""
    params = action["params"]
    obstacles = [tuple(ob) for ob in params["obstacles"]]
    metrics = run_episode(obstacles, tuple(params["goal"]))
    metrics.pop("trajectory")
    RESULT_FILE.write_text(json.dumps(metrics, indent=1))
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    catalog = load_catalog()
    session = zenoh.open(zenoh.Config())
    succeeded = []      # successful episode metrics (for --once)
    seen_keys = set()   # idempotency keys already handled

    def publish_result(result):
        session.put(RESULT_TOPIC, json.dumps(result))
        log(f"result -> {RESULT_TOPIC}: {json.dumps(result)[:120]}")

    def on_sample(sample):
        try:
            event = json.loads(bytes(sample.payload))
        except ValueError:
            log(f"ignoring non-JSON payload on {ACTION_TOPIC}")
            return
        action = event.get("payload") or {}
        base = {"actionId": action.get("actionId", "unknown"),
                "skill": action.get("skillId", "unknown")}

        key = action.get("idempotencyKey") or base["actionId"]
        if key in seen_keys:
            log(f"replay of idempotencyKey {key!r}: NOT re-executing")
            publish_result({**base, "status": "error", "error": {
                "code": "DUPLICATE",
                "message": f"idempotencyKey {key!r} was already executed"}})
            return
        seen_keys.add(key)

        error = validate(action, catalog)
        if error:
            code, message = error
            log(f"rejected action {base['actionId']}: {code}: {message}")
            publish_result({**base, "status": "error",
                            "error": {"code": code, "message": message}})
            return

        log(f"action {base['actionId']}: executing {base['skill']}, "
            f"payment={json.dumps(action.get('payment'))[:80]}")
        try:
            metrics = execute(action)
        except ValueError as exc:   # planner: no collision-free path exists
            log(f"action {base['actionId']} failed: {exc}")
            publish_result({**base, "status": "error", "error": {
                "code": "NO_PATH", "message": str(exc)}})
            return
        if not metrics["reached"]:
            publish_result({**base, "status": "error", "error": {
                "code": "ACTION_FAILED",
                "message": f"episode ended {metrics['final_goal_distance_m']} m "
                           f"from the goal (fell={metrics['fell']})"}})
            return
        publish_result({**base, "status": "success", "result": metrics})
        succeeded.append(metrics)

    session.declare_subscriber(ACTION_TOPIC, on_sample)
    log(f"listening on '{ACTION_TOPIC}', results on '{RESULT_TOPIC}'")
    try:
        while not (args.once and succeeded):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()


if __name__ == "__main__":
    main()
