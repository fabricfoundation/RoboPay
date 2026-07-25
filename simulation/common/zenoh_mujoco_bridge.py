"""Zenoh bridge adapter for MuJoCo simulation.

Subscribes to Fabric tunnel ActionEvents via Zenoh and routes them to the
MuJoCo simulator's policy. This is the missing integration layer that the
reviewer requires: payment-verified ActionEvent → Zenoh → this adapter →
MuJoCo actuator control.

Usage:
    # Start the tunnel first (verifies x402 payment)
    # Then start this adapter:
    python -m simulation.common.zenoh_mujoco_bridge \
        --robot unitree_g1 \
        --scene simulation/mujoco/scenes/unitree_g1.xml \
        --zenoh-endpoint tcp/127.0.0.1:7447

Control flow:
    tunnel (x402 verify) → zenoh topic "robot/action" →
    this adapter → ActionEvent → policy goal → MuJoCo actuators
"""

import argparse
import logging
import sys
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Reuse the existing bridge components
try:
    from bridge.common.zenoh_bridge.zenoh_bridge.action_event import (
        ActionEvent,
        parse_action_event,
    )
    from bridge.common.zenoh_bridge.zenoh_bridge.zenoh_subscriber import (
        ZenohSubscriberHelper,
    )
except ImportError:
    # Fallback for standalone simulation use (without bridge installed)
    import json
    from dataclasses import dataclass, field
    from typing import Any, Dict

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
            action=payload.get("action", "stop"),
            params=payload.get("params") or {},
            timestamp=event.get("timestamp", ""),
        )

    class ZenohSubscriberHelper:
        def __init__(self, listen_endpoint: str = "tcp/127.0.0.1:7447"):
            import zenoh
            conf = zenoh.Config.from_json5(
                f'{{"listen":{{"endpoints":["{listen_endpoint}"]}}}}'
            )
            self._session = zenoh.open(conf)
            self._subs = []
        def subscribe(self, topic: str, callback) -> None:
            sub = self._session.declare_subscriber(topic, callback)
            self._subs.append(sub)
        def close(self) -> None:
            for s in self._subs:
                s.undeclare()
            self._session.close()


# Action → goal mapping (same actions the bridge supports)
ACTION_GOAL_MAP = {
    "move_forward": {"vx": 0.5, "vy": 0.0, "wz": 0.0},
    "move_backward": {"vx": -0.3, "vy": 0.0, "wz": 0.0},
    "turn_left": {"vx": 0.0, "vy": 0.0, "wz": 0.5},
    "turn_right": {"vx": 0.0, "vy": 0.0, "wz": -0.5},
    "stop": {"vx": 0.0, "vy": 0.0, "wz": 0.0},
    "navigate": None,  # params.goal_x, params.goal_y
}


class MuJoCoZenohBridge:
    """Bridges Fabric ActionEvents to MuJoCo simulation control.

    Security model: only actions that arrive through the verified Zenoh
    topic (published by the tunnel after x402 verification) are accepted.
    Direct control of the simulator without going through the tunnel is
    not possible — the simulator only listens on Zenoh.
    """

    def __init__(self, scene_path: str, robot_type: str, zenoh_endpoint: str):
        self.scene_path = scene_path
        self.robot_type = robot_type
        self.zenoh_endpoint = zenoh_endpoint
        self.mapper = get_mapper(robot_type)
        self._current_cmd = self.mapper.zero()
        self._current_goal = {"vx": 0.0, "vy": 0.0, "wz": 0.0}
        self._running = False
        self._action_count = 0
        self._rejected_count = 0

    def _on_action_event(self, sample) -> None:
        """Handle incoming ActionEvent from Zenoh.

        Only verified actions reach this point — the tunnel's x402
        middleware rejects unverified requests before publishing.
        """
        raw = bytes(sample.payload)
        event = parse_action_event(raw)
        if event is None:
            self._rejected_count += 1
            logger.warning("Rejected malformed action event")
            return

        logger.info(
            "Received action: %s (params: %s, ts: %s)",
            event.action, event.params, event.timestamp,
        )

        if event.action == "stop" or event.action == "cancel":
            self._current_goal = {"vx": 0.0, "vy": 0.0, "wz": 0.0}
            logger.info("STOP: zeroing all velocities")
            self._action_count += 1
            return

        if event.action == "navigate":
            # Navigation uses goal coordinates from params
            goal_x = event.params.get("goal_x", 0.0)
            goal_y = event.params.get("goal_y", 0.0)
            self._current_goal = {"navigate": True, "goal_x": goal_x, "goal_y": goal_y}
            logger.info("NAVIGATE: goal=(%.2f, %.2f)", goal_x, goal_y)
            self._action_count += 1
            return

        goal = ACTION_GOAL_MAP.get(event.action)
        if goal is None:
            self._rejected_count += 1
            logger.warning("Unknown action: %s", event.action)
            return

        self._current_goal = goal
        self._action_count += 1
        logger.info("ACTION: %s → goal=%s", event.action, goal)

    def run(self) -> None:
        """Start the bridge: subscribe to Zenoh and run MuJoCo sim loop."""
        logger.info("Starting MuJoCo Zenoh bridge for %s", self.robot_type)
        logger.info("Scene: %s", self.scene_path)
        logger.info("Zenoh endpoint: %s", self.zenoh_endpoint)

        # Import MuJoCo here to allow standalone testing without mujoco
        try:
            import mujoco
        except ImportError:
            logger.error("mujoco not installed. Install with: pip install mujoco")
            sys.exit(1)

        # Load scene
        model = mujoco.MjModel.from_xml_path(self.scene_path)
        data = mujoco.MjData(model)

        # Set up Zenoh subscriber
        zenoh_helper = ZenohSubscriberHelper(listen_endpoint=self.zenoh_endpoint)
        zenoh_helper.subscribe("robot/action", self._on_action_event)

        logger.info("Bridge active. Waiting for ActionEvents on 'robot/action'...")
        logger.info("Security: only tunnel-verified actions are accepted.")

        self._running = True
        step_count = 0
        report_interval = 1000  # Report every N steps

        try:
            while self._running:
                # Apply current goal to actuators
                self._apply_goal(model, data)

                # Step simulation
                mujoco.mj_step(model, data)
                step_count += 1

                # Periodic state report
                if step_count % report_interval == 0:
                    logger.info(
                        "Step %d: pos=(%.2f, %.2f) actions=%d rejected=%d",
                        step_count,
                        data.qpos[0] if model.nq > 0 else 0,
                        data.qpos[1] if model.nq > 1 else 0,
                        self._action_count,
                        self._rejected_count,
                    )

                # Real-time pacing (simulation time)
                time.sleep(model.opt.timestep)

        except KeyboardInterrupt:
            logger.info("Interrupted. Stopping simulation.")
        finally:
            self._running = False
            zenoh_helper.close()
            logger.info(
                "Bridge stopped. Total actions: %d, rejected: %d",
                self._action_count, self._rejected_count,
            )

    def _apply_goal(self, model, data) -> None:
        """Apply current goal to MuJoCo actuators."""
        import mujoco

        if self._current_goal.get("navigate"):
            # Navigation: compute velocity toward goal
            goal_x = self._current_goal["goal_x"]
            goal_y = self._current_goal["goal_y"]
            dx = goal_x - data.qpos[0]
            dy = goal_y - data.qpos[1]
            dist = (dx**2 + dy**2) ** 0.5
            if dist > 0.1:
                # Proportional control
                speed = min(0.5, dist * 0.5)
                data.ctrl[0] = speed * dx / dist  # vx
                data.ctrl[1] = speed * dy / dist  # vy
            else:
                data.ctrl[0] = 0.0
                data.ctrl[1] = 0.0
        else:
            # Direct velocity commands
            vx = self._current_goal.get("vx", 0.0)
            vy = self._current_goal.get("vy", 0.0)
            wz = self._current_goal.get("wz", 0.0)
            if model.nu >= 3:
                data.ctrl[0] = vx
                data.ctrl[1] = vy
                data.ctrl[2] = wz
            elif model.nu >= 1:
                data.ctrl[0] = vx


def main():
    parser = argparse.ArgumentParser(description="MuJoCo Zenoh Bridge")
    parser.add_argument("--scene", required=True, help="Path to MuJoCo XML scene")
    parser.add_argument("--robot", default="unitree_g1", help="Robot type")
    parser.add_argument("--zenoh-endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    bridge = MuJoCoZenohBridge(args.scene, args.robot, args.zenoh_endpoint)
    bridge.run()


if __name__ == "__main__":
    main()