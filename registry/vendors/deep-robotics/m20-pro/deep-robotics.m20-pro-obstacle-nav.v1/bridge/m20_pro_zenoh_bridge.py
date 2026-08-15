"""Deep Robotics M20 Pro Zenoh bridge -- the RoboPay integration gate.

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
import sys
import time

import zenoh

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)
sys.path.insert(0, os.path.join(THIS_DIR, ".."))

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
from simulation.runners.m20_pro_runner import M20ProMuJoCoRunner  # noqa: E402

PROFILE_ROOT = os.path.normpath(os.path.join(THIS_DIR, ".."))
SCENE_PATH = os.path.join(PROFILE_ROOT, "simulation", "scenes", "m20_pro.xml")
DEFAULT_DB_PATH = os.path.join(PROFILE_ROOT, "bridge", "replay_guard.db")

ACTION_TOPIC = "robot/tunnel/action"
RESULT_TOPIC = "robot/tunnel/result"

ROBOT_ID = "deep-robotics-m20-pro-sim-01"
SKILL_ID = "m20_pro_obstacle_navigation"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [m20-pro-bridge] %(levelname)s: %(message)s",
)
log = logging.getLogger("m20_pro_bridge")


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


class M20ProBridge:
    def __init__(self, db_path: str = DEFAULT_DB_PATH, scene_path: str = SCENE_PATH):
        self.guard = ReplayGuard(db_path)
        self.runner = M20ProMuJoCoRunner(scene_path=scene_path)
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
            log.warning("Dropping unparseable/incomplete action event on %s", ACTION_TOPIC)
            return

        action_id = event.action_id

        if event.action != SKILL_ID:
            log.warning("Rejected action_id=%s: unknown skill %r (this bridge only serves %r)",
                         action_id, event.action, SKILL_ID)
            self._publish(make_result(action_id, "rejected", errorCode="unknown_skill"))
            return

        params = event.params
        if "target_xy" not in params:
            log.warning("Rejected action_id=%s: params missing target_xy", action_id)
            self._publish(make_result(action_id, "rejected", errorCode="invalid_params"))
            return

        fp = Fingerprint(
            action_id=action_id,
            robot_id=ROBOT_ID,
            skill_id=event.action,
            params_hash=json.dumps(params, sort_keys=True),
        )
        try:
            self.guard.check_and_reserve(fp)
        except ReplayDetected as e:
            log.warning("Rejected replay for action_id=%s: %s", action_id, e)
            self._publish(make_result(action_id, "rejected", errorCode="replay_detected", errorMessage=str(e)))
            return

        log.info("Dispatching action_id=%s to MuJoCo simulator with params=%s", action_id, params)
        try:
            metrics = self.runner.run_episode(params)
        except Exception as e:  # noqa: BLE001
            log.error("Simulator dispatch failed for action_id=%s: %s", action_id, e)
            self.guard.record_result(action_id, "error")
            self._publish(make_result(action_id, "error", errorCode="simulator_failure", errorMessage=str(e)))
            return

        sim_status = metrics.get("status")
        is_success = sim_status == "goal_reached" and metrics.get("collisions", 1) == 0
        result_status = "success" if is_success else "error"
        self.guard.record_result(action_id, result_status)
        self._publish(make_result(
            action_id, result_status,
            robotId=ROBOT_ID,
            skillId=event.action,
            simulatorStatus=sim_status,
            metrics=metrics,
        ))

    def handle_stop(self, action_id: str) -> dict:
        """Stop/cancel requires no payment (the tunnel does not gate this
        path) and always succeeds immediately."""
        self.runner.stop()
        return make_result(action_id, "success", robotId=ROBOT_ID, skillId=SKILL_ID, reason="stopped")


def main():
    bridge = M20ProBridge()
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
