"""Webots Supervisor controller for the AgiBot X2 obstacle-avoidance episode.

Reads episode parameters from environment variables and writes a JSON
result file on termination. Uses the same policy as the MuJoCo runner.
"""

import json
import os
import sys

sys.path.insert(0, os.environ["ROBOPAY_POLICY_PATH"])
from obstacle_avoid_policy import ObstacleAvoidPolicy, Observation  # noqa: E402

from controller import Supervisor  # noqa: E402


TIME_STEP = 20  # ms, matches WorldInfo.basicTimeStep
COLLISION_MARGIN = 0.05
ROBOT_RADIUS = 0.20
OBSTACLE_RADIUS = 0.18


def main() -> int:
    target_x = float(os.environ["ROBOPAY_TARGET_X"])
    target_y = float(os.environ["ROBOPAY_TARGET_Y"])
    max_duration_sec = float(os.environ["ROBOPAY_MAX_DURATION_SEC"])
    result_path = os.environ["ROBOPAY_RESULT_FILE"]

    supervisor = Supervisor()
    robot_node = supervisor.getSelf()
    # must match mujoco_runner.OBSTACLE_COURSE
    obstacle_positions = [(0.8, 0.15), (1.4, -0.2), (2.0, 0.1)]

    policy = ObstacleAvoidPolicy()
    max_steps = int((max_duration_sec * 1000) / TIME_STEP)

    result = None
    step_count = 0
    while supervisor.step(TIME_STEP) != -1:
        translation = robot_node.getField("translation").getSFVec3f()
        robot_x, robot_y = translation[0], translation[1]

        obs = Observation(
            robot_x=robot_x, robot_y=robot_y,
            target_x=target_x, target_y=target_y,
            obstacle_positions=tuple(obstacle_positions),
        )

        if policy.reached_target(obs):
            result = {
                "reached_target": True, "collided": False, "timed_out": False,
                "simulator": "webots",
                "detail": f"reached target in {step_count * TIME_STEP / 1000:.2f}s",
            }
            break

        collided = any(
            ((robot_x - ox) ** 2 + (robot_y - oy) ** 2) ** 0.5
            < (OBSTACLE_RADIUS + ROBOT_RADIUS + COLLISION_MARGIN)
            for ox, oy in obstacle_positions
        )
        if collided:
            result = {
                "reached_target": False, "collided": True, "timed_out": False,
                "simulator": "webots", "detail": "collided with obstacle",
            }
            break

        cmd = policy.act(obs)
        robot_node.setVelocity([cmd.vx, cmd.vy, 0, 0, 0, 0])

        step_count += 1
        if step_count >= max_steps:
            result = {
                "reached_target": False, "collided": False, "timed_out": True,
                "simulator": "webots",
                "detail": "max_duration_sec elapsed before reaching target",
            }
            break

    if result is None:
        result = {
            "reached_target": False, "collided": False, "timed_out": True,
            "simulator": "webots", "detail": "simulation ended unexpectedly",
        }

    with open(result_path, "w") as f:
        json.dump(result, f)

    supervisor.simulationQuit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
