"""RoboPay -> Go2 simulation link: execute paid robot actions in MuJoCo.

Subscribes to the Zenoh topic the RoboPay tunnel publishes paid actions to
(handlers.go, both the x402 and AIP rails publish there), validates the
action envelope against the skill catalog and the x402 payment gate, runs
the Go2 skill on the mujoco_menagerie model, and publishes a structured
result correlated by actionId on the result topic.

Wire contract (documented in ../README.md):
  action topic  ROBOPAY_ACTION_TOPIC  default robot/tunnel/action
  result topic  ROBOPAY_RESULT_TOPIC  default robot/tunnel/result
  robot id      ROBOPAY_ROBOT_ID      default test-robot (tunnel config.json)

Success result: {"status": "success", "actionId", "skill", "result": {...}}
Error result:   {"status": "error", "actionId", "skill",
                 "error": {"code", "message"}}
Error codes: UNKNOWN_SKILL, INVALID_PARAMS, WRONG_ROBOT, ACTION_FAILED,
             UNPAID, REJECTED_PAYMENT, DUPLICATE. A replayed idempotencyKey
             is never re-executed; the relay must not settle on any error
             result (see payment_gate.py).

Usage: python3 robopay_link.py [--once]
  --once: exit after the first successful action (used by the e2e test)
"""

import argparse
import json
import os
import pathlib
import time

import zenoh

from go2_control import Go2Controller
from payment_gate import PaymentGate

ACTION_TOPIC = os.environ.get("ROBOPAY_ACTION_TOPIC", "robot/tunnel/action")
RESULT_TOPIC = os.environ.get("ROBOPAY_RESULT_TOPIC", "robot/tunnel/result")
ROBOT_ID = os.environ.get("ROBOPAY_ROBOT_ID", "test-robot")

HERE = pathlib.Path(__file__).parent
SKILLS_FILE = HERE / "skills.json"
MODEL_PATH = os.environ.get("GO2_MODEL_PATH",
                            str(HERE.parent / "models" / "mujoco_menagerie"
                                / "unitree_go2" / "scene.xml"))
RESULT_FILE = HERE / "last_action_result.json"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def params_hash(params):
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib_sha256(canonical)


def hashlib_sha256(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


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
        if spec["type"] == "angle":
            if not isinstance(value, (int, float)) \
                    or abs(value) > spec["absMax"]:
                return "INVALID_PARAMS", \
                    f"{name!r} must be degrees with |v| <= {spec['absMax']}"
        if spec["type"] == "number":
            if not isinstance(value, (int, float)) \
                    or value < spec.get("min", -1e9) \
                    or value > spec.get("max", 1e9):
                return "INVALID_PARAMS", f"{name!r} out of range"
    return None


class Link:
    def __init__(self, model_path=MODEL_PATH, once=False):
        self.controller = Go2Controller(model_path)
        self.gate = PaymentGate()
        self.once = once
        self.succeeded = []
        self.seen_keys = set()

    def publish_result(self, session, result):
        session.put(RESULT_TOPIC, json.dumps(result))
        log(f"result -> {RESULT_TOPIC}: {json.dumps(result)[:160]}")

    def handle(self, session, event):
        action = event.get("payload") or {}
        base = {"actionId": action.get("actionId", "unknown"),
                "skill": action.get("skillId", "unknown")}

        key = action.get("idempotencyKey") or base["actionId"]
        if key in self.seen_keys:
            log(f"replay of idempotencyKey {key!r}: NOT re-executing")
            self.publish_result(session, {**base, "status": "error", "error": {
                "code": "DUPLICATE",
                "message": f"idempotencyKey {key!r} was already executed"}})
            return

        ok, status, reason = self.gate.check(action)
        if not ok:
            code = "REJECTED_PAYMENT" if status != 402 else "UNPAID"
            log(f"payment gate {status}: {reason}")
            self.publish_result(session, {**base, "status": "error", "error": {
                "code": code, "message": f"HTTP {status}: {reason}"}})
            return
        self.seen_keys.add(key)

        error = validate(action, self.catalog)
        if error:
            code, message = error
            log(f"rejected action {base['actionId']}: {code}: {message}")
            self.publish_result(session, {**base, "status": "error",
                                          "error": {"code": code,
                                                    "message": message}})
            return

        log(f"action {base['actionId']}: executing {base['skill']}, "
            f"payment={json.dumps(action.get('payment'))[:80]}")
        result = self.controller.execute(base["skill"],
                                         action.get("params") or {})
        payload = result.to_dict()
        payload["actionId"] = base["actionId"]
        payload["skill"] = base["skill"]
        if result.status == "success":
            self.gate.decide_settlement("success", base["actionId"])
        self.publish_result(session, payload)
        self.succeeded.append(result.metrics)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    link = Link(once=args.once)
    link.catalog = load_catalog()
    log(f"controller ready on {MODEL_PATH}")
    session = zenoh.open(zenoh.Config())

    def on_sample(sample):
        try:
            event = json.loads(bytes(sample.payload))
        except ValueError:
            log(f"ignoring non-JSON payload on {ACTION_TOPIC}")
            return
        link.handle(session, event)

    session.declare_subscriber(ACTION_TOPIC, on_sample)
    log(f"listening on '{ACTION_TOPIC}', results on '{RESULT_TOPIC}'")
    try:
        while not (args.once and link.succeeded):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()


if __name__ == "__main__":
    main()
