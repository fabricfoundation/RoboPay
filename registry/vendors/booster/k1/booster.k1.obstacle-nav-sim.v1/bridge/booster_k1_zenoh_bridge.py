"""
Booster K1 Zenoh bridge -- the RoboPay integration gate.

Flow (matches execution-mapping.yaml / functions.yaml):

    Fabric backend -> Tunnel (x402 verify) -> Zenoh (robot/tunnel/action)
        -> [THIS BRIDGE]
            1. validate envelope + payment (action_validator.py)
            2. check replay (replay_guard.py) -- reserve BEFORE dispatch
            3. dispatch to MuJoCo simulator (simulation/mujoco/runner.py)
            4. publish terminal result correlated by actionId on
               robot/tunnel/result

No fallback path: if Zenoh is unreachable, if the envelope fails
validation, or if replay is detected, the action is REJECTED. There
is no direct-simulator-write path that bypasses this gate -- this is
the only way a payload can reach simulation/mujoco/runner.py through
this bridge.

Settlement gating (payment-policy.yaml): the result published here
carries status=success only when the simulator actually reports
status=success. A downstream settlement service is expected to only
settle on status=success, and never settle on error/pending/timeout/
rejected -- this bridge enforces that by construction, since it is
the sole source of truth for the result's status field.
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time

import zenoh

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)
from action_validator import validate_envelope, ValidationError  # noqa: E402
from replay_guard import ReplayGuard, ReplayDetected, Fingerprint  # noqa: E402

PROFILE_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))
MUJOCO_RUNNER = os.path.join(PROFILE_ROOT, "simulation", "mujoco", "runner.py")
MUJOCO_DIR = os.path.join(PROFILE_ROOT, "simulation", "mujoco")
DEFAULT_DB_PATH = os.path.join(PROFILE_ROOT, "bridge", "replay_guard.db")

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [booster-k1-bridge] %(levelname)s: %(message)s",
)
log = logging.getLogger("booster_k1_bridge")


def make_result(action_id: str, status: str, **extra) -> dict:
    """Every published result is correlated to the originating actionId,
    per the correlationField requirement in execution-mapping.yaml."""
    result = {
        "schemaVersion": "robot-action-result.v1",
        "actionId": action_id,
        "status": status,
        "publishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    result.update(extra)
    return result


def dispatch_to_simulator(params: dict) -> dict:
    """Runs the MuJoCo policy-driven simulation for this action's
    params and returns the resulting metrics dict. Raises
    subprocess.CalledProcessError if the runner itself fails."""
    goal_x = params["goal_x"]
    goal_y = params["goal_y"]
    max_time_sec = params.get("max_time_sec", 60)

    proc = subprocess.run(
        [sys.executable, MUJOCO_RUNNER,
         "--goal_x", str(goal_x), "--goal_y", str(goal_y),
         "--max_time_sec", str(max_time_sec)],
        cwd=MUJOCO_DIR, capture_output=True, text=True, timeout=max_time_sec + 30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"simulator exited with code {proc.returncode}: {proc.stderr}")

    metrics_path = os.path.join(MUJOCO_DIR, "results", "metrics.json")
    with open(metrics_path) as f:
        return json.load(f)


class BoosterK1Bridge:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.guard = ReplayGuard(db_path)
        self.session = None
        self.publisher = None

    def start(self):
        log.info("Opening Zenoh session...")
        self.session = zenoh.open(zenoh.Config())
        self.publisher = self.session.declare_publisher(RESULT_TOPIC)
        self.session.declare_subscriber(ACTION_TOPIC, self._on_action)
        log.info("Subscribed to %s, publishing results to %s", ACTION_TOPIC, RESULT_TOPIC)

    def stop(self):
        if self.session is not None:
            self.session.close()
        self.guard.close()

    def _publish(self, result: dict):
        payload = json.dumps(result).encode("utf-8")
        self.publisher.put(payload)
        log.info("Published result actionId=%s status=%s", result["actionId"], result["status"])

    def _on_action(self, sample):
        raw = bytes(sample.payload)
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("Rejecting malformed JSON on %s: %s", ACTION_TOPIC, e)
            # No actionId available to correlate -- cannot publish a
            # terminal result without one. Drop and log only.
            return

        action_id = envelope.get("actionId", "<unknown>")

        # --- STAGE 1: validate envelope + payment ---
        try:
            validated = validate_envelope(envelope)
        except ValidationError as e:
            log.warning("Rejected action_id=%s: %s", action_id, e)
            self._publish(make_result(
                action_id, "rejected", errorCode=e.code, errorMessage=e.message,
            ))
            return

        # --- STAGE 2: replay protection (reserve BEFORE dispatch) ---
        fp = Fingerprint(
            action_id=validated.action_id,
            robot_id=validated.robot_id,
            skill_id=validated.skill_id,
            params_hash=envelope["paramsHash"],
            authorization_id=validated.authorization_id,
        )
        try:
            self.guard.check_and_reserve(validated.idempotency_key, fp)
        except ReplayDetected as e:
            log.warning("Rejected replay for action_id=%s: %s", action_id, e)
            self._publish(make_result(
                action_id, "rejected", errorCode="replay_detected", errorMessage=str(e),
            ))
            return

        # --- STAGE 3: dispatch to simulator ---
        log.info("Dispatching action_id=%s to MuJoCo simulator with params=%s",
                  action_id, validated.params)
        try:
            metrics = dispatch_to_simulator(validated.params)
        except Exception as e:
            log.error("Simulator dispatch failed for action_id=%s: %s", action_id, e)
            self.guard.record_result(validated.idempotency_key, "error")
            self._publish(make_result(
                action_id, "error", errorCode="simulator_failure", errorMessage=str(e),
            ))
            return

        # --- STAGE 4: publish terminal result, gated by simulator status ---
        sim_status = metrics.get("status")
        result_status = "success" if sim_status == "success" else "error"
        self.guard.record_result(validated.idempotency_key, result_status)
        self._publish(make_result(
            action_id, result_status,
            robotId=validated.robot_id,
            skillId=validated.skill_id,
            simulatorStatus=sim_status,
            metrics={
                "distance_to_goal_m": metrics.get("distance_to_goal_m"),
                "path_length_m": metrics.get("path_length_m"),
                "collision_count": metrics.get("collision_count"),
                "sim_time_sec": metrics.get("sim_time_sec"),
            },
        ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    bridge = BoosterK1Bridge(db_path=args.db_path)
    bridge.start()
    log.info("Bridge running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
