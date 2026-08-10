"""Physics-backed MuJoCo station using the official Booster K1 model."""

from __future__ import annotations

import mujoco
import numpy as np

from .model import model_assets, resolve_model_dir


MODEL_COMMIT = "508cbee6ca9ae6fbc8c0b38dd58785a6f3fc61a2"


def _station_xml() -> str:
    return """<mujoco model="robopay_booster_k1_inspection_station">
      <include file="K1_22dof.xml"/>
      <visual><global azimuth="145" elevation="-16"/></visual>
      <worldbody>
        <body name="target_left" pos="0.75 0.70 0.85"><geom type="sphere" size="0.055" contype="0" conaffinity="0" rgba="0.95 0.2 0.15 1"/></body>
        <body name="target_center" pos="0.90 0 0.90"><geom type="sphere" size="0.055" contype="0" conaffinity="0" rgba="0.15 0.85 0.25 1"/></body>
        <body name="target_right" pos="0.75 -0.70 0.85"><geom type="sphere" size="0.055" contype="0" conaffinity="0" rgba="0.15 0.35 0.95 1"/></body>
        <body name="support_post" pos="-0.12 0 0.275"><geom type="box" size="0.05 0.06 0.275" rgba="0.25 0.27 0.30 1"/></body>
      </worldbody>
      <equality><weld name="inspection_support" body1="Trunk"/></equality>
    </mujoco>"""


class K1InspectionEnvironment:
    def __init__(self, model_dir: str | None = None):
        directory = resolve_model_dir(model_dir)
        self.model = mujoco.MjModel.from_xml_string(_station_xml(), assets=model_assets(directory, trunk_height=0.55))
        self.data = mujoco.MjData(self.model)
        self.qpos_addresses = np.array([self.model.jnt_qposadr[self.model.actuator_trnid[i, 0]] for i in range(self.model.nu)])
        self.dof_addresses = np.array([self.model.jnt_dofadr[self.model.actuator_trnid[i, 0]] for i in range(self.model.nu)])
        self.trunk_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "Trunk")
        self.left_hand_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "left_hand_link")
        self.right_hand_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "right_hand_link")
        self.initial_hands: tuple[np.ndarray, np.ndarray] | None = None
        self.max_joint_speed = 0.0
        self.max_hand_motion = [0.0, 0.0]

    def reset(self) -> dict:
        mujoco.mj_resetData(self.model, self.data)
        self.data.qpos[self.qpos_addresses[3]] = -1.30
        self.data.qpos[self.qpos_addresses[7]] = 1.30
        mujoco.mj_forward(self.model, self.data)
        self.initial_hands = (self.data.xpos[self.left_hand_id].copy(), self.data.xpos[self.right_hand_id].copy())
        self.max_joint_speed = 0.0
        self.max_hand_motion = [0.0, 0.0]
        return self.observe()

    def observe(self) -> dict:
        positions = self.data.qpos[self.qpos_addresses].copy()
        velocities = self.data.qvel[self.dof_addresses].copy()
        return {
            "sim_time": float(self.data.time),
            "joint_positions": positions,
            "joint_velocities": velocities,
            "upper_joint_positions": positions[:10],
            "trunk_position": self.data.xpos[self.trunk_id].copy(),
            "left_hand_position": self.data.xpos[self.left_hand_id].copy(),
            "right_hand_position": self.data.xpos[self.right_hand_id].copy(),
        }

    def step(self, torque: np.ndarray) -> dict:
        if torque.shape != (22,):
            raise ValueError(f"Expected 22 motor torques, got {torque.shape}.")
        self.data.ctrl[:] = np.clip(torque, self.model.actuator_forcerange[:, 0], self.model.actuator_forcerange[:, 1])
        mujoco.mj_step(self.model, self.data)
        self.max_joint_speed = max(self.max_joint_speed, float(np.max(np.abs(self.data.qvel[self.dof_addresses]))))
        if self.initial_hands is not None:
            self.max_hand_motion[0] = max(self.max_hand_motion[0], float(np.linalg.norm(self.data.xpos[self.left_hand_id] - self.initial_hands[0])))
            self.max_hand_motion[1] = max(self.max_hand_motion[1], float(np.linalg.norm(self.data.xpos[self.right_hand_id] - self.initial_hands[1])))
        return self.observe()

    def safe_stop(self, torque: np.ndarray) -> dict:
        self.data.ctrl[:] = np.clip(torque, self.model.actuator_forcerange[:, 0], self.model.actuator_forcerange[:, 1])
        self.data.qvel[self.dof_addresses] = 0.0
        mujoco.mj_forward(self.model, self.data)
        return self.observe()
