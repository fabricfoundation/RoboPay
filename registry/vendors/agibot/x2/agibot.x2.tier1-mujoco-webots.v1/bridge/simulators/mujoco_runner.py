"""MuJoCo episode runner for the obstacle-avoidance navigation skill."""

from __future__ import annotations

import sys
from pathlib import Path

import mujoco

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policy.obstacle_avoid_policy import ObstacleAvoidPolicy, Observation  # noqa: E402
from agibot_x2_tier1_bridge import SimOutcome  # noqa: E402

# Fixed obstacle positions (x, y, radius), shared with the Webots world.
OBSTACLE_COURSE = (
    (0.8, 0.15, 0.18),
    (1.4, -0.2, 0.18),
    (2.0, 0.1, 0.18),
)

CONTROL_DT = 0.05  # seconds between policy decisions
COLLISION_MARGIN = 0.05  # meters, added to obstacle radius + robot radius
ROBOT_RADIUS = 0.20


class MujocoRunner:
    def __init__(self, model_path: str) -> None:
        self.model_path = model_path
        self._model = None
        self._data = None
        self._policy = ObstacleAvoidPolicy()

    def _load_fresh(self) -> None:
        self._model = mujoco.MjModel.from_xml_path(self.model_path)
        self._data = mujoco.MjData(self._model)
        mujoco.mj_forward(self._model, self._data)

    def run_episode(self, *, target_x: float, target_y: float, max_duration_sec: float) -> SimOutcome:
        self._load_fresh()
        model, data = self._model, self._data

        steps_per_control = max(1, int(CONTROL_DT / model.opt.timestep))
        max_steps = int(max_duration_sec / model.opt.timestep)

        drive_x = model.actuator("drive_x").id
        drive_y = model.actuator("drive_y").id
        base_x_qpos = model.joint("base_x").qposadr[0]
        base_y_qpos = model.joint("base_y").qposadr[0]

        step_count = 0
        while step_count < max_steps:
            robot_x = float(data.qpos[base_x_qpos])
            robot_y = float(data.qpos[base_y_qpos])

            obs = Observation(
                robot_x=robot_x, robot_y=robot_y,
                target_x=target_x, target_y=target_y,
                obstacle_positions=tuple((ox, oy) for ox, oy, _ in OBSTACLE_COURSE),
            )

            if self._policy.reached_target(obs):
                return SimOutcome(
                    reached_target=True, collided=False, timed_out=False,
                    simulator="mujoco",
                    detail=f"reached target in {step_count * model.opt.timestep:.2f}s",
                )

            if self._check_collision(robot_x, robot_y):
                return SimOutcome(
                    reached_target=False, collided=True, timed_out=False,
                    simulator="mujoco", detail="collided with obstacle",
                )

            cmd = self._policy.act(obs)
            data.ctrl[drive_x] = cmd.vx
            data.ctrl[drive_y] = cmd.vy

            for _ in range(steps_per_control):
                mujoco.mj_step(model, data)
                step_count += 1
                if step_count >= max_steps:
                    break

        return SimOutcome(
            reached_target=False, collided=False, timed_out=True,
            simulator="mujoco", detail="max_duration_sec elapsed before reaching target",
        )

    @staticmethod
    def _check_collision(robot_x: float, robot_y: float) -> bool:
        for ox, oy, radius in OBSTACLE_COURSE:
            dist = ((robot_x - ox) ** 2 + (robot_y - oy) ** 2) ** 0.5
            if dist < (radius + ROBOT_RADIUS + COLLISION_MARGIN):
                return True
        return False

    def close(self) -> None:
        self._model = None
        self._data = None
