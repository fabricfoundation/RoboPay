"""MuJoCo adapter for Reachy Mini — consumes /cmd_vel from existing bridge.

Architecture (matches RoboPay documented flow):
    Tunnel (x402) → Zenoh → existing bridge → /cmd_vel → this adapter → MuJoCo

Reachy Mini is a small desktop arm robot (not a humanoid walker).
Skills: wave (arm motion), look (head pan/tilt), grasp (gripper close).
"""

import argparse
import json
import logging
import math
import os
import sys
import time

logger = logging.getLogger(__name__)

# Reachy Mini skills (arm-based, not locomotion)
REACHY_SKILLS = {
    "wave": {"joint": "r_shoulder_pitch", "amplitude": 0.8, "frequency": 2.0},
    "look": {"joint": "head_pan", "amplitude": 0.5, "frequency": 1.0},
    "grasp": {"joint": "gripper", "close": True},
    "release": {"joint": "gripper", "close": False},
    "stop": {},
}


class MuJoCoCmdVelAdapter:
    """Subscribes to /cmd_vel (Twist) and drives Reachy Mini MuJoCo model.

    This is NOT a replacement for the existing bridge. It is a
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
        self._current_skill = None

    def _on_cmd_vel(self, sample):
        """Handle /cmd_vel Twist message from the existing bridge."""
        try:
            raw = bytes(sample.payload)
            msg = json.loads(raw)
            linear = msg.get("linear", {})
            angular = msg.get("angular", {})
            self._vx = float(linear.get("x", 0.0))
            self._vy = float(linear.get("y", 0.0))
            self._wz = float(angular.get("z", 0.0))
            self._action_count += 1
            logger.info("cmd_vel: vx=%.2f vy=%.2f wz=%.2f", self._vx, self._vy, self._wz)
        except Exception as e:
            logger.warning("Failed to parse cmd_vel: %s", e)

    def _on_action(self, sample):
        """Handle ActionEvent for skill-based control."""
        try:
            raw = bytes(sample.payload)
            msg = json.loads(raw)
            payload = msg.get("payload", msg)
            skill_id = payload.get("skillId", payload.get("action", "stop"))
            params = payload.get("params", {})
            action_id = payload.get("actionId", "unknown")

            logger.info("Action: %s | params: %s", skill_id, params)
            self._current_skill = skill_id
            self._action_count += 1

            # Publish result
            result = {
                "actionId": action_id,
                "robotId": self.robot_id,
                "status": "success",
                "skill": skill_id,
                "result": {"message": f"Action {skill_id} completed"},
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if hasattr(self, "_zenoh_session"):
                self._zenoh_session.put("robot/tunnel/result", json.dumps(result))
                logger.info("Result published: success")
        except Exception as e:
            logger.warning("Failed to parse action: %s", e)

    def run(self):
        """Start the adapter."""
        import mujoco

        try:
            import zenoh
        except ImportError:
            logger.error("zenoh-py not installed: pip install zenoh-py")
            sys.exit(1)

        logger.info("Starting Reachy Mini MuJoCo adapter")
        logger.info("Robot: %s | Scene: %s", self.robot_id, self.scene_path)
        logger.info("Architecture: existing bridge -> /cmd_vel -> this adapter -> MuJoCo")

        model = mujoco.MjModel.from_xml_path(self.scene_path)
        model.vis.global_.offwidth = 960
        model.vis.global_.offheight = 540
        data = mujoco.MjData(model)

        conf = zenoh.Config.from_json5(
            '{"connect":{"endpoints":["tcp/127.0.0.1:7447"]}}'
        )
        self._zenoh_session = zenoh.open(conf)
        sub_cmdvel = self._zenoh_session.declare_subscriber("/cmd_vel", self._on_cmd_vel)
        sub_action = self._zenoh_session.declare_subscriber("robot/tunnel/action", self._on_action)

        logger.info("Adapter active. Subscribing to /cmd_vel and robot/tunnel/action")
        self._running = True
        step = 0

        try:
            while self._running:
                # Apply skill-based control
                if self._current_skill == "wave":
                    phase = step * 0.1
                    data.ctrl[0] = -1.0 + 0.5 * math.sin(phase)  # shoulder pitch
                elif self._current_skill == "look":
                    phase = step * 0.05
                    data.ctrl[2] = 0.3 * math.sin(phase)  # head pan
                elif self._current_skill == "grasp":
                    data.ctrl[3] = -0.5  # close gripper
                elif self._current_skill == "release":
                    data.ctrl[3] = 0.0  # open gripper
                elif self._current_skill == "stop":
                    data.ctrl[:] = 0.0
                else:
                    # Default: apply cmd_vel to base movement
                    data.ctrl[0] = self._vx
                    data.ctrl[1] = self._vy

                mujoco.mj_step(model, data)
                step += 1

                if step % 500 == 0:
                    pos = data.qpos[:3]
                    logger.info(
                        "Step %d | pos=(%.2f,%.2f,%.2f) | skill=%s | actions=%d",
                        step, pos[0], pos[1], pos[2],
                        self._current_skill or "idle",
                        self._action_count,
                    )

                time.sleep(model.opt.timestep)

        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self._running = False
            sub_cmdvel.undeclare()
            sub_action.undeclare()
            self._zenoh_session.close()
            logger.info("Adapter stopped. Total actions: %d", self._action_count)


def main():
    parser = argparse.ArgumentParser(description="Reachy Mini MuJoCo Adapter")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--robot-id", default="reachy-mini-demo-001")
    parser.add_argument("--zenoh-endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [REACHY-ADAPTER] %(message)s",
    )

    adapter = MuJoCoCmdVelAdapter(args.scene, args.robot_id, args.zenoh_endpoint)
    adapter.run()


if __name__ == "__main__":
    main()
