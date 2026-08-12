"""Booster K1 Zenoh bridge -- the RoboPay integration gate.

    Tunnel (x402 verify, fail-closed) -> robot/tunnel/action -> [this bridge]
        parse (shared action_event.py) -> replay-check -> dispatch to
        MuJoCo -> robot/tunnel/result

Payment verification and settlement are handled entirely by the Go
tunnel (tunnel/internal/handlers): it verifies the x402 payment before
ever publishing to robot/tunnel/action, and it alone calls
ProcessSettlement, only after this bridge's result confirms success.
This bridge does not re-verify payment -- it trusts that every event
on robot/tunnel/action already passed the tunnel's fail-closed gate,
and its only remaining job is: don't dispatch malformed events, don't
dispatch replays, and report a truthful terminal result correlated by
actionId so the tunnel's execution watcher can decide whether to
settle.
"""
import json
import logging
import os
import subprocess
import sys
import time

import zenoh

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

# Import action_event.py directly by file path rather than as
# `zenoh_bridge.action_event` -- the zenoh_bridge package's __init__.py
# also imports command_mapper.py, which requires geometry_msgs (a ROS2
# dependency this simulator-only bridge does not need).
import importlib.util

_ACTION_EVENT_PATH = os.path.normpath(os.path.join(
    THIS_DIR, "..", "..", "..", "..", "..", "..",
    "bridge", "common", "zenoh_bridge", "zenoh_bridge", "action_event.py",
))
_spec = importlib.util.spec_from_file_location("action_event", _ACTION_EVENT_PATH)
_action_event_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_action_event_module)
parse_action_event = _action_event_module.parse_action_event

from replay_guard import ReplayGuard, ReplayDetected, Fingerprint  # noqa: E402

PROFILE_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))
MUJOCO_RUNNER = os.path.join(PROFILE_ROOT, "simulation", "mujoco", "runner.py")
MUJOCO_DIR = os.path.join(PROFILE_ROOT, "simulation", "mujoco")
DEFAULT_DB_PATH = os.path.join(PROFILE_ROOT, "bridge", "replay_guard.db")

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"

SKILL_ID = "k1_navigate_avoid_obstacles"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [booster-k1-bridge] %(levelname)s: %(message)s",
)
log = logging.getLogger("booster_k1_bridge")


def make_result(action_id: str, status: str, **extra) -> dict:
    """Every result carries the originating actionId, so the tunnel's
    execution watcher can correlate it back to the reserved payment
    record and decide whether to settle."""
    result = {
        "schemaVersion": "robot-action-result.v1",
        "actionId": action_id,
        "status": status,
        "publishedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    result.update(extra)
    return result


def dispatch_to_simulator(params: dict) -> dict:
    """Runs the MuJoCo simulation for this action and returns its metrics."""
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
        event = parse_action_event(raw)
        if event is None:
            # No actionId to correlate a result with (either the JSON was
            # malformed, or the tunnel's own gate somehow let through an
            # event missing actionId/action -- either way there is nothing
            # to publish a terminal result against). Log and drop.
            log.warning("Dropping unparseable/incomplete action event on %s", ACTION_TOPIC)
            return

        action_id = event.action_id

        if event.action != SKILL_ID:
            log.warning("Rejected action_id=%s: unknown skill %r (this bridge only serves %r)",
                         action_id, event.action, SKILL_ID)
            self._publish(make_result(action_id, "rejected", errorCode="unknown_skill"))
            return

        params = event.params
        if "goal_x" not in params or "goal_y" not in params:
            log.warning("Rejected action_id=%s: params missing goal_x/goal_y", action_id)
            self._publish(make_result(action_id, "rejected", errorCode="invalid_params"))
            return

        fp = Fingerprint(
            action_id=action_id,
            robot_id="booster-k1-sim-01",
            skill_id=event.action,
            params_hash=json.dumps(params, sort_keys=True),
            authorization_id=action_id,  # tunnel owns the real authorizationId; this
                                          # bridge only needs a unique key to prevent
                                          # dispatching the same actionId twice.
        )
        try:
            self.guard.check_and_reserve(action_id, fp)
        except ReplayDetected as e:
            log.warning("Rejected replay for action_id=%s: %s", action_id, e)
            self._publish(make_result(action_id, "rejected", errorCode="replay_detected", errorMessage=str(e)))
            return

        log.info("Dispatching action_id=%s to MuJoCo simulator with params=%s", action_id, params)
        try:
            metrics = dispatch_to_simulator(params)
        except Exception as e:
            log.error("Simulator dispatch failed for action_id=%s: %s", action_id, e)
            self.guard.record_result(action_id, "error")
            self._publish(make_result(action_id, "error", errorCode="simulator_failure", errorMessage=str(e)))
            return

        sim_status = metrics.get("status")
        result_status = "success" if sim_status == "success" else "error"
        self.guard.record_result(action_id, result_status)
        self._publish(make_result(
            action_id, result_status,
            robotId="booster-k1-sim-01",
            skillId=event.action,
            simulatorStatus=sim_status,
            metrics={
                "distance_to_goal_m": metrics.get("distance_to_goal_m"),
                "path_length_m": metrics.get("path_length_m"),
                "collision_count": metrics.get("collision_count"),
                "sim_time_sec": metrics.get("sim_time_sec"),
            },
        ))


def main():
    bridge = BoosterK1Bridge()
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
