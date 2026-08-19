"""MuJoCo course built around the pinned MuJoCo humanoid model.

Phase 2: Contact classification, body-height guard, foot-contact state.
"""

from __future__ import annotations

import math
import mujoco
import numpy as np

from .course import COURSE_GOAL, COURSE_OBSTACLES, COURSE_START_YAW_RAD
from .model import resolve_model_dir

FALL_THRESHOLD_M = 0.05
FLOOR_GEOM_NAME = "floor"
RIGHT_FOOT_BODY = "r_foot"
LEFT_FOOT_BODY = "l_foot"


def _yaw(q: np.ndarray) -> float:
    w, x, y, z = q
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def classify_contacts(model: mujoco.MjModel, data: mujoco.MjData,
                      obstacle_geom_ids: set[int],
                      floor_geom_id: int,
                      right_foot_geom_ids: set[int],
                      left_foot_geom_ids: set[int]) -> dict:
    ground = 0
    obstacle = 0
    self_contact = 0
    right_foot_ground = False
    left_foot_ground = False
    for i in range(data.ncon):
        c = data.contact[i]
        g1, g2 = c.geom1, c.geom2
        pair = {g1, g2}
        if floor_geom_id in pair:
            ground += 1
            if g1 in right_foot_geom_ids or g2 in right_foot_geom_ids:
                right_foot_ground = True
            if g1 in left_foot_geom_ids or g2 in left_foot_geom_ids:
                left_foot_ground = True
        elif pair & obstacle_geom_ids:
            obstacle += 1
        else:
            self_contact += 1
    return {
        "ground_contacts": ground,
        "obstacle_contacts": obstacle,
        "self_contacts": self_contact,
        "right_foot_on_ground": right_foot_ground,
        "left_foot_on_ground": left_foot_ground,
    }


class AtlasObstacleCourseEnvironment:
    def __init__(self, model_dir: str | None = None) -> None:
        model_path = resolve_model_dir(model_dir) / "scene_atlas_working.xml"
        spec = mujoco.MjSpec.from_file(str(model_path))
        for item in COURSE_OBSTACLES:
            body = spec.worldbody.add_body(
                name=item["name"],
                pos=[item["x"], item["y"], item["height"] / 2.0],
            )
            body.add_geom(
                name=f'{item["name"]}_geom',
                type=mujoco.mjtGeom.mjGEOM_BOX,
                size=[item["half_x"], item["half_y"], item["height"] / 2.0],
                rgba=[0.82, 0.16, 0.08, 1.0],
                friction=[1.2, 0.02, 0.01],
            )
        goal = spec.worldbody.add_body(
            name="goal_marker",
            pos=[COURSE_GOAL[0], COURSE_GOAL[1], 0.015],
        )
        goal.add_geom(
            type=mujoco.mjtGeom.mjGEOM_CYLINDER,
            size=[0.22, 0.015, 0.0],
            rgba=[0.1, 0.8, 0.25, 0.55],
            contype=0,
            conaffinity=0,
        )
        self.model = spec.compile()
        self.data = mujoco.MjData(self.model)
        self.base_body_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, "pelvis"
        )
        self.joint_ids = self.model.actuator_trnid[:, 0]
        self.qpos_addresses = self.model.jnt_qposadr[self.joint_ids]
        self.qvel_addresses = self.model.jnt_dofadr[self.joint_ids]

        self._floor_geom_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_GEOM, FLOOR_GEOM_NAME
        )
        self._obstacle_geom_ids = {
            mujoco.mj_name2id(
                self.model, mujoco.mjtObj.mjOBJ_GEOM, f'{item["name"]}_geom'
            )
            for item in COURSE_OBSTACLES
        }
        self._right_foot_geom_ids = set()
        self._left_foot_geom_ids = set()
        right_foot_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, RIGHT_FOOT_BODY
        )
        left_foot_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, LEFT_FOOT_BODY
        )
        for i in range(self.model.ngeom):
            if self.model.geom_bodyid[i] == right_foot_id:
                self._right_foot_geom_ids.add(i)
            elif self.model.geom_bodyid[i] == left_foot_id:
                self._left_foot_geom_ids.add(i)

        self.path_length = 0.0
        self.min_clearance = float("inf")
        self.min_body_height = float("inf")
        self.max_body_height = 0.0
        self.fall_detected = False
        self._previous: np.ndarray | None = None

    def reset(self, neutral: np.ndarray) -> dict:
        mujoco.mj_resetData(self.model, self.data)

        standing_qpos = np.zeros(self.model.nq)
        standing_qpos[0] = 0.0
        standing_qpos[1] = 0.0
        standing_qpos[2] = 1.34
        standing_qpos[3] = 1.0
        standing_qpos[4] = 0.0
        standing_qpos[5] = 0.0
        standing_qpos[6] = 0.0

        for i, jnt_id in enumerate(self.joint_ids):
            jnt_name = self.model.joint(jnt_id).name
            if jnt_name in NEUTRAL_POSE:
                standing_qpos[self.qpos_addresses[i]] = NEUTRAL_POSE[jnt_name]

        self.data.qpos[:] = standing_qpos
        mujoco.mj_forward(self.model, self.data)

        self._previous = self.data.xpos[self.base_body_id].copy()
        self.path_length = 0.0
        self.min_clearance = float("inf")
        self.min_body_height = float(self.data.xpos[self.base_body_id][2])
        self.max_body_height = float(self.data.xpos[self.base_body_id][2])
        self.fall_detected = False
        return self.observe()

    def _clearance(self, p: np.ndarray) -> float:
        return min(
            math.hypot(
                max(abs(p[0] - o["x"]) - o["half_x"], 0.0),
                max(abs(p[1] - o["y"]) - o["half_y"], 0.0),
            )
            for o in COURSE_OBSTACLES
        )

    def observe(self) -> dict:
        p = self.data.xpos[self.base_body_id].copy()
        body_z = float(p[2])
        self.min_body_height = min(self.min_body_height, body_z)
        self.max_body_height = max(self.max_body_height, body_z)
        if body_z < FALL_THRESHOLD_M:
            self.fall_detected = True

        contacts = classify_contacts(
            self.model, self.data,
            self._obstacle_geom_ids, self._floor_geom_id,
            self._right_foot_geom_ids, self._left_foot_geom_ids,
        )

        torso_xmat = self.data.xmat[self.base_body_id].reshape(3, 3)
        torso_pitch = math.atan2(
            -torso_xmat[2, 0],
            math.sqrt(torso_xmat[2, 1] ** 2 + torso_xmat[2, 2] ** 2),
        )
        torso_roll = math.atan2(torso_xmat[2, 1], torso_xmat[2, 2])
        torso_vel = self.data.cvel[self.base_body_id].copy()

        return {
            "sim_time": float(self.data.time),
            "position": p,
            "yaw": _yaw(self.data.qpos[3:7]),
            "body_height": body_z,
            "torso_pitch": torso_pitch,
            "torso_roll": torso_roll,
            "linear_velocity": torso_vel[:3].copy(),
            "angular_velocity": torso_vel[3:].copy(),
            "goal": COURSE_GOAL,
            "clearance": self._clearance(p),
            "ground_contacts": contacts["ground_contacts"],
            "obstacle_contacts": contacts["obstacle_contacts"],
            "self_contacts": contacts["self_contacts"],
            "right_foot_on_ground": contacts["right_foot_on_ground"],
            "left_foot_on_ground": contacts["left_foot_on_ground"],
        }

    def step(self, torque: np.ndarray) -> dict:
        self.data.ctrl[:] = torque
        mujoco.mj_step(self.model, self.data)
        p = self.data.xpos[self.base_body_id].copy()
        self.path_length += float(np.linalg.norm(p[:2] - self._previous[:2]))
        self._previous = p
        self.min_clearance = min(self.min_clearance, self._clearance(p))
        return self.observe()

    def measured_joints(self) -> tuple[np.ndarray, np.ndarray]:
        return (
            self.data.qpos[self.qpos_addresses].copy(),
            self.data.qvel[self.qvel_addresses].copy(),
        )

    def safe_stop(self, neutral: np.ndarray) -> dict:
        self.data.ctrl[:] = 0.0
        self.data.qpos[self.qpos_addresses] = neutral
        self.data.qvel[:] = 0.0
        mujoco.mj_forward(self.model, self.data)
        p = self.data.xpos[self.base_body_id].copy()
        self._previous = p
        self.min_clearance = min(self.min_clearance, self._clearance(p))
        return self.observe()


NEUTRAL_POSE = {
    "back_bkz": 0.0,
    "back_bky": 0.15,
    "back_bkx": 0.0,
    "l_leg_hpz": 0.02,
    "l_leg_hpx": 0.05,
    "l_leg_hpy": -0.3,
    "l_leg_kny": 0.6,
    "l_leg_aky": -0.3,
    "l_leg_akx": -0.05,
    "r_leg_hpz": -0.02,
    "r_leg_hpx": -0.05,
    "r_leg_hpy": -0.3,
    "r_leg_kny": 0.6,
    "r_leg_aky": -0.3,
    "r_leg_akx": 0.05,
    "l_arm_shz": 0.0,
    "l_arm_shx": 0.0,
    "l_arm_ely": 0.5,
    "l_arm_elx": -0.5,
    "l_arm_uwy": 0.0,
    "l_arm_mwx": 0.0,
    "l_arm_lwy": 0.0,
    "r_arm_shz": 0.0,
    "r_arm_shx": 0.0,
    "r_arm_ely": 0.5,
    "r_arm_elx": -0.5,
    "r_arm_uwy": 0.0,
    "r_arm_mwx": 0.0,
    "r_arm_lwy": 0.0,
    "neck_ay": 0.0,
}
