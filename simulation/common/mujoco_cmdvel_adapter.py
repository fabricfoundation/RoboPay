'''MuJoCo adapter that consumes /cmd_vel from the existing G1 bridge.

Architecture (matches RoboPay documented flow):
    Tunnel (x402) → Zenoh → existing G1 bridge → /cmd_vel → this adapter → MuJoCo

This adapter subscribes to the /cmd_vel topic published by the existing
ROS2 bridge and translates Twist messages into MuJoCo actuator commands.

Usage:
    # 1. Start tunnel
    # 2. Start existing G1 bridge (ROS2)
    # 3. Start this adapter
    python simulation/common/mujoco_cmdvel_adapter.py \
        --scene simulation/mujoco/scenes/unitree_g1.xml
'''

import argparse
import json
import logging
import math
import os
import sys
import time

logger = logging.getLogger(__name__)


class MuJoCoCmdVelAdapter:
    """Subscribes to /cmd_vel (Twist) and drives MuJoCo actuators.

    This is NOT a replacement for the existing G1 bridge. It is a
    downstream consumer of /cmd_vel that the bridge publishes.
    """

    def __init__(self, scene_path: str, robot_id: str, zenoh_endpoint: str):
        self.scene_path = scene_path
        self.robot_id = robot_id
        self.zenoh_endpoint = zenoh_endpoint
        self._vx = 0.0
        self._vy = 0.0
        self._wz = 0.0
        self._running = False
        self._action_count = 0

    def _on_cmd_vel(self, sample):
        """Handle /cmd_vel Twist message from the existing bridge."""
        try:
            raw = bytes(sample.payload)
            msg = json.loads(raw)
            # Twist format: {linear: {x, y, z}, angular: {x, y, z}}
            linear = msg.get("linear", {})
            angular = msg.get("angular", {})
            self._vx = float(linear.get("x", 0.0))
            self._vy = float(linear.get("y", 0.0))
            self._wz = float(angular.get("z", 0.0))
            self._action_count += 1
            logger.info("cmd_vel: vx=%.2f vy=%.2f wz=%.2f", self._vx, self._vy, self._wz)
        except Exception as e:
            logger.warning("Failed to parse cmd_vel: %s", e)

    def run(self):
        """Start the adapter."""
        import mujoco

        try:
            import zenoh
        except ImportError:
            logger.error("zenoh-py not installed: pip install zenoh-py")
            sys.exit(1)

        logger.info("Starting MuJoCo cmd_vel adapter")
        logger.info("Robot: %s | Scene: %s", self.robot_id, self.scene_path)
        logger.info("Subscribing to /cmd_vel from existing G1 bridge")

        model = mujoco.MjModel.from_xml_path(self.scene_path)
        model.vis.global_.offwidth = 960
        model.vis.global_.offheight = 540
        data = mujoco.MjData(model)

        # Subscribe to /cmd_vel (published by existing bridge)
        conf = zenoh.Config.from_json5(
            '{"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}'
        )
        session = zenoh.open(conf)
        sub = session.declare_subscriber("/cmd_vel", self._on_cmd_vel)

        logger.info("Adapter active. Waiting for /cmd_vel messages...")
        self._running = True
        step = 0

        try:
            while self._running:
                # Apply cmd_vel to MuJoCo freejoint actuators
                data.ctrl[0] = self._vx
                data.ctrl[1] = self._vy
                data.ctrl[2] = self._wz

                mujoco.mj_step(model, data)
                step += 1

                if step % 500 == 0:
                    pos = data.qpos[:3]
                    logger.info(
                        "Step %d | pos=(%.2f,%.2f,%.2f) | cmd_vel=(%.2f,%.2f,%.2f) | actions=%d",
                        step, pos[0], pos[1], pos[2],
                        self._vx, self._vy, self._wz,
                        self._action_count,
                    )

                time.sleep(model.opt.timestep)

        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self._running = False
            sub.undeclare()
            session.close()
            logger.info("Adapter stopped. Total cmd_vel messages: %d", self._action_count)


def main():
    parser = argparse.ArgumentParser(description="MuJoCo cmd_vel Adapter")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--robot-id", default="g1-demo-001")
    parser.add_argument("--zenoh-endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [CMDVEL-ADAPTER] %(message)s",
    )

    adapter = MuJoCoCmdVelAdapter(args.scene, args.robot_id, args.zenoh_endpoint)
    adapter.run()


if __name__ == "__main__":
    main()
