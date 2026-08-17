"""Run one task in a simulator and report what happened.

Kept separate from both the Zenoh bridge and the policy so that each can be
exercised alone: the bridge can be tested against a stub runner with no
simulator, and the simulator can be driven from a script with no payment
plumbing. The engine is selectable so the same task can be replayed in a
second back end for sim-to-sim validation.
"""

from __future__ import annotations

import time
from typing import Callable

import numpy as np

from ..x2.mapper import TaskSpec
from ..policy.controller import X2PushPolicy, PolicyConfig
from ..policy.ik import ArmIK
from ..policy.stages import Stage
from .base import SimEnv
from .metrics import RunMetrics, StageTiming
from .mujoco_env import MujocoX2Env

#: Builds an environment for a given puck and goal. One per engine.
EnvFactory = Callable[[float, float, float, float], SimEnv]

#: Hard cap on control ticks, so a stuck policy cannot spin forever.
MAX_TICKS = 24_000


def mujoco_factory(px: float, py: float, gx: float, gy: float) -> SimEnv:
    return MujocoX2Env(puck_x=px, puck_y=py, goal_x=gx, goal_y=gy)


class TaskRunner:
    """Executes TaskSpecs against a simulator, reusing one IK plant."""

    def __init__(
        self,
        factory: EnvFactory = mujoco_factory,
        engine: str = "mujoco",
        config: PolicyConfig | None = None,
        frame_sink: Callable[[np.ndarray], None] | None = None,
        frame_every: int = 20,
    ) -> None:
        self._factory = factory
        self._engine = engine
        self._config = config or PolicyConfig()
        # Building the Drake plant costs a second or so; do it once.
        self._ik = ArmIK()
        self._frame_sink = frame_sink
        self._frame_every = max(1, frame_every)

    def __call__(self, task: TaskSpec) -> RunMetrics:
        return self.run(task)

    def run(self, task: TaskSpec) -> RunMetrics:
        if task.puck_xy is None or task.goal_xy is None:
            raise ValueError(f"skill {task.skill_id!r} has no motion to run")

        px, py = task.puck_xy
        gx, gy = task.goal_xy
        started = time.perf_counter()
        env = self._factory(px, py, gx, gy)
        try:
            obs = env.reset()
            policy = X2PushPolicy(
                env.joint_limits, self._ik, env.goal, self._config
            )
            policy.reset(obs)
            start_xy = (float(obs.object_pos[0]), float(obs.object_pos[1]))

            peak_contacts = 0
            peak_force = 0.0
            foreign = False
            status = None

            for tick in range(MAX_TICKS):
                cmd, status = policy.step(obs)
                obs = env.step(cmd)
                peak_contacts = max(peak_contacts, obs.hand_contacts)
                peak_force = max(peak_force, obs.grasp_force)
                foreign = foreign or obs.self_collision
                if self._frame_sink is not None and tick % self._frame_every == 0:
                    self._frame_sink(env.render())
                if status.terminal:
                    break

            end_xy = (float(obs.object_pos[0]), float(obs.object_pos[1]))
            reason = status.reason if status else "policy produced no status"
            success = bool(status and status.success)
            if self._frame_sink is not None:
                self._frame_sink(env.render())

            return RunMetrics(
                engine=self._engine,
                success=success,
                reason=None if success else (reason or "did not reach the goal"),
                puck_start=start_xy,
                puck_end=end_xy,
                goal=(float(env.goal[0]), float(env.goal[1])),
                displacement=float(
                    np.linalg.norm(np.array(end_xy) - np.array(start_xy))
                ),
                final_distance=float(status.goal_distance) if status else 0.0,
                tolerance=self._config.goal_tolerance,
                peak_contacts=peak_contacts,
                peak_contact_force=peak_force,
                foreign_collision=foreign,
                sim_seconds=float(obs.t),
                wall_seconds=time.perf_counter() - started,
                stages=[
                    StageTiming(h.stage, h.duration, h.error)
                    for h in (status.history if status else [])
                ],
            )
        finally:
            env.close()


def stage_names() -> tuple[str, ...]:
    """Plan stages, for documentation and tests."""
    return tuple(s.value for s in Stage if s not in (Stage.DONE, Stage.FAILED))
