"""Zenoh bridge adapter for MuJoCo simulation — RoboPay integration.

Subscribes to Fabric tunnel ActionEvents via Zenoh and routes them to the
MuJoCo simulator. Publishes execution results back to Zenoh.

End-to-end flow:
    Fabric → Tunnel (x402 verify) → Zenoh (robot/tunnel/action) →
    this bridge → MuJoCo actuators → state metrics →
    Zenoh (robot/tunnel/result) → Fabric

Usage:
    python -m simulation.common.zenoh_mujoco_bridge \
        --scene simulation/mujoco/scenes/unitree_g1.xml \
        --robot-id g1-demo-001
"""

import argparse
import json
import logging
import sys
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Import ActionEvent from bridge
try:
    from bridge.common.zenoh_bridge.zenoh_bridge.action_event import (
        ActionEvent,
        parse_action_event,
    )
except ImportError:
    # Standalone fallback
    from dataclasses import dataclass, field

    @dataclass
    class ActionEvent:
        action: str
        params: Dict[str, Any] = field(default_factory=dict)
        timestamp: str = ""

    def parse_action_event(raw: bytes) -> Optional[ActionEvent]:
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            return None
        return ActionEvent(
            action=payload.get("action", payload.get("skillId", "stop")),
            params=payload.get("params") or {},
            timestamp=event.get("timestamp", ""),
        )


ACTION_SKILL_MAP = {
    "move_forward": {"vx": 0.5, "vy": 0.0, "wz": 0.0, "skill": "move_forward"},
    "move_backward": {"vx": -0.3, "vy": 0.0, "wz": 0.0, "skill": "move_backward"},
    "turn_left": {"vx": 0.0, "vy": 0.0, "wz": 0.5, "skill": "turn_left"},
    "turn_right": {"vx": 0.0, "vy": 0.0, "wz": -0.5, "skill": "turn_right"},
    "navigate_obstacle": {"navigate": True, "skill": "navigate_obstacle"},
    "pick_and_place": {"pick_place": True, "skill": "pick_and_place"},
    "stop": {"vx": 0.0, "vy": 0.0, "wz": 0.0, "skill": "stop"},
    "cancel": {"vx": 0.0, "vy": 0.0, "wz": 0.0, "skill": "stop"},
}


class MuJoCoZenohBridge:
    """Bridges Fabric ActionEvents to MuJoCo simulation.

    Security: only tunnel-verified actions reach this bridge.
    The tunnel's x402 middleware rejects unverified requests before publishing.
    """

    def __init__(self, scene_path: str, robot_id: str, zenoh_endpoint: str):
        self.scene_path = scene_path
        self.robot_id = robot_id
        self.zenoh_endpoint = zenoh_endpoint
        self._current_goal: Dict[str, Any] = {"vx": 0.0, "vy": 0.0, "wz": 0.0}
        self._running = False
        self._action_count = 0
        self._rejected_count = 0
        self._start_pos = None

    def _on_action_event(self, sample) -> None:
        """Handle incoming ActionEvent from Zenoh."""
        raw = bytes(sample.payload)
        event = parse_action_event(raw)
        if event is None:
            self._rejected_count += 1
            logger.warning("Rejected malformed action event")
            return

        logger.info("Action: %s | params: %s", event.action, event.params)

        # Extract actionId for result correlation
        try:
            envelope = json.loads(raw)
            action_id = envelope.get("payload", {}).get("actionId", "unknown")
            idempotency_key = envelope.get("payload", {}).get("idempotencyKey", "")
        except Exception:
            action_id = "unknown"
            idempotency_key = ""

        # Check for duplicate idempotency
        if idempotency_key and hasattr(self, '_seen_keys'):
            if idempotency_key in self._seen_keys:
                self._publish_result(action_id, "already_executed", None)
                return
        if not hasattr(self, '_seen_keys'):
            self._seen_keys = set()
        if idempotency_key:
            self._seen_keys.add(idempotency_key)

        goal = ACTION_SKILL_MAP.get(event.action)
        if goal is None:
            self._rejected_count += 1
            logger.warning("Unknown skill: %s", event.action)
            self._publish_result(action_id, "error", {
                "code": "UNKNOWN_SKILL",
                "message": f"Skill '{event.action}' not found",
            })
            return

        self._current_goal = {**goal, **event.params}
        self._current_goal["action_id"] = action_id
        self._action_count += 1

    def _publish_result(self, action_id: str, status: str, error: Optional[dict]) -> None:
        """Publish execution result to Zenoh."""
        if not hasattr(self, '_zenoh_session'):
            return

        result = {
            "actionId": action_id,
            "robotId": self.robot_id,
            "status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if error:
            result["error"] = error
        else:
            result["result"] = {"message": "Action completed"}

        self._zenoh_session.put("robot/tunnel/result", json.dumps(result))
        logger.info("Result published: %s", status)

    def run(self) -> None:
        """Start the bridge."""
        logger.info("Starting MuJoCo Zenoh bridge")
        logger.info("Robot: %s | Scene: %s", self.robot_id, self.scene_path)

        try:
            import mujoco
        except ImportError:
            logger.error("mujoco not installed: pip install mujoco")
            sys.exit(1)

        try:
            import zenoh
        except ImportError:
            logger.error("zenoh-py not installed: pip install zenoh-py")
            sys.exit(1)

        # Load MuJoCo scene
        model = mujoco.MjModel.from_xml_path(self.scene_path)
        data = mujoco.MjData(model)
        self._start_pos = data.qpos[:3].copy() if model.nq >= 3 else data.qpos.copy()

        # Connect Zenoh
        conf = zenoh.Config.from_json5(
            f'{{"listen":{{"endpoints":["{self.zenoh_endpoint}"]}}}}'
        )
        self._zenoh_session = zenoh.open(conf)
        sub = self._zenoh_session.declare_subscriber("robot/tunnel/action", self._on_action_event)

        logger.info("Bridge active on robot/tunnel/action")
        logger.info("Results published to robot/tunnel/result")
        logger.info("Security: only tunnel-verified actions accepted")

        self._running = True
        step_count = 0
        report_interval = 500
        last_action_id = None

        try:
            while self._running:
                self._apply_goal(model, data)
                mujoco.mj_step(model, data)
                step_count += 1

                # Check if current action completed
                current_action_id = self._current_goal.get("action_id")
                if current_action_id and current_action_id != last_action_id:
                    last_action_id = current_action_id

                # Periodic metrics
                if step_count % report_interval == 0:
                    pos = data.qpos[:3] if model.nq >= 3 else data.qpos
                    displacement = sum((pos[i] - self._start_pos[i])**2 for i in range(min(3, len(pos)))) ** 0.5
                    collision = self._check_collision(data)
                    logger.info(
                        "Step %d | pos=(%.2f, %.2f) | disp=%.2fm | collision=%s | actions=%d",
                        step_count, pos[0], pos[1], displacement, collision, self._action_count,
                    )

                    # Auto-complete navigate actions when goal reached
                    if self._current_goal.get("navigate"):
                        goal_x = self._current_goal.get("goal_x", 0)
                        goal_y = self._current_goal.get("goal_y", 0)
                        dx = goal_x - pos[0]
                        dy = goal_y - pos[1]
                        if (dx**2 + dy**2)**0.5 < 0.3:
                            self._publish_result(last_action_id, "success", None)
                            self._current_goal = {"vx": 0, "vy": 0, "wz": 0}

                time.sleep(model.opt.timestep)

        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self._running = False
            sub.undeclare()
            self._zenoh_session.close()
            logger.info("Bridge stopped. actions=%d rejected=%d", self._action_count, self._rejected_count)

    def _apply_goal(self, model, data) -> None:
        """Apply current goal to MuJoCo actuators."""
        if self._current_goal.get("navigate"):
            goal_x = self._current_goal.get("goal_x", 0)
            goal_y = self._current_goal.get("goal_y", 0)
            dx = goal_x - data.qpos[0]
            dy = goal_y - data.qpos[1]
            dist = (dx**2 + dy**2)**0.5
            if dist > 0.1:
                speed = min(0.5, dist * 0.5)
                if model.nu >= 2:
                    data.ctrl[0] = speed * dx / dist
                    data.ctrl[1] = speed * dy / dist
            else:
                if model.nu >= 2:
                    data.ctrl[0] = 0
                    data.ctrl[1] = 0
        else:
            vx = self._current_goal.get("vx", 0.0)
            vy = self._current_goal.get("vy", 0.0)
            wz = self._current_goal.get("wz", 0.0)
            if model.nu >= 3:
                data.ctrl[0] = vx
                data.ctrl[1] = vy
                data.ctrl[2] = wz
            elif model.nu >= 1:
                data.ctrl[0] = vx

    def _check_collision(self, data) -> bool:
        """Check for collisions in simulation."""
        if hasattr(data, 'contact') and data.ncon > 0:
            for i in range(data.ncon):
                contact = data.contact[i]
                if contact.geom1 != 0 or contact.geom2 != 0:
                    return True
        return False


def main():
    parser = argparse.ArgumentParser(description="MuJoCo Zenoh Bridge")
    parser.add_argument("--scene", required=True, help="MuJoCo XML scene path")
    parser.add_argument("--robot-id", default="g1-demo-001", help="Robot ID")
    parser.add_argument("--zenoh-endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    bridge = MuJoCoZenohBridge(args.scene, args.robot_id, args.zenoh_endpoint)
    bridge.run()


if __name__ == "__main__":
    main()
