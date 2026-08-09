"""MuJoCo course built around Unitree's pinned official Go2 MJCF."""

from __future__ import annotations

import math
import mujoco
import numpy as np

from .course import COURSE_GOAL, COURSE_OBSTACLES, COURSE_START_YAW_RAD
from .model import resolve_model_dir


def _yaw(q: np.ndarray) -> float:
    w, x, y, z = q
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


class Go2ObstacleCourseEnvironment:
    def __init__(self, model_dir: str | None = None) -> None:
        model_path = resolve_model_dir(model_dir) / "go2.xml"
        spec = mujoco.MjSpec.from_file(str(model_path))
        spec.worldbody.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[0, 0, 0.05], rgba=[0.18, 0.22, 0.27, 1.0])
        for item in COURSE_OBSTACLES:
            body = spec.worldbody.add_body(name=item["name"], pos=[item["x"], item["y"], item["height"] / 2.0])
            body.add_geom(name=f'{item["name"]}_geom', type=mujoco.mjtGeom.mjGEOM_BOX, size=[item["half_x"], item["half_y"], item["height"] / 2.0], rgba=[0.82, 0.16, 0.08, 1.0], friction=[1.2, 0.02, 0.01])
        goal = spec.worldbody.add_body(name="goal_marker", pos=[COURSE_GOAL[0], COURSE_GOAL[1], 0.015])
        goal.add_geom(type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.22, 0.015, 0.0], rgba=[0.1, 0.8, 0.25, 0.55], contype=0, conaffinity=0)
        self.model = spec.compile()
        self.data = mujoco.MjData(self.model)
        self.base_body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "base_link")
        self.joint_ids = self.model.actuator_trnid[:, 0]
        self.qpos_addresses = self.model.jnt_qposadr[self.joint_ids]
        self.qvel_addresses = self.model.jnt_dofadr[self.joint_ids]
        self.obstacle_geom_ids = {
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, f'{item["name"]}_geom')
            for item in COURSE_OBSTACLES
        }
        self.path_length = 0.0
        self.collision_count = 0
        self.min_clearance = float("inf")
        self._previous: np.ndarray | None = None

    def reset(self, neutral: np.ndarray) -> dict:
        mujoco.mj_resetData(self.model, self.data)
        self.data.qpos[:7] = [0.0, 0.0, 0.32, math.cos(COURSE_START_YAW_RAD / 2.0), 0.0, 0.0, math.sin(COURSE_START_YAW_RAD / 2.0)]
        self.data.qpos[self.qpos_addresses] = neutral
        mujoco.mj_forward(self.model, self.data)
        self._previous = self.data.xpos[self.base_body_id].copy()
        self.path_length = 0.0
        self.collision_count = 0
        self.min_clearance = float("inf")
        return self.observe()

    def _clearance(self, p: np.ndarray) -> float:
        return min(math.hypot(max(abs(p[0] - o["x"]) - o["half_x"], 0.0), max(abs(p[1] - o["y"]) - o["half_y"], 0.0)) for o in COURSE_OBSTACLES)

    def _obstacle_contacts(self) -> int:
        return sum(1 for i in range(self.data.ncon) if self.data.contact[i].geom1 in self.obstacle_geom_ids or self.data.contact[i].geom2 in self.obstacle_geom_ids)

    def observe(self) -> dict:
        p = self.data.xpos[self.base_body_id].copy()
        return {"sim_time": float(self.data.time), "position": p, "yaw": _yaw(self.data.qpos[3:7]), "body_height": float(p[2]), "goal": COURSE_GOAL, "clearance": self._clearance(p), "collision_count": self.collision_count}

    def step(self, torque: np.ndarray) -> dict:
        self.data.ctrl[:] = torque
        mujoco.mj_step(self.model, self.data)
        p = self.data.xpos[self.base_body_id].copy()
        self.path_length += float(np.linalg.norm(p[:2] - self._previous[:2]))
        self._previous = p
        self.collision_count += self._obstacle_contacts()
        self.min_clearance = min(self.min_clearance, self._clearance(p))
        return self.observe()

    def measured_joints(self) -> tuple[np.ndarray, np.ndarray]:
        return self.data.qpos[self.qpos_addresses].copy(), self.data.qvel[self.qvel_addresses].copy()

    def safe_stop(self, neutral: np.ndarray) -> dict:
        self.data.ctrl[:] = 0.0
        self.data.qpos[self.qpos_addresses] = neutral
        self.data.qvel[:] = 0.0
        mujoco.mj_forward(self.model, self.data)
        p = self.data.xpos[self.base_body_id].copy()
        self._previous = p
        self.min_clearance = min(self.min_clearance, self._clearance(p))
        return self.observe()
