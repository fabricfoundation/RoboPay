"""Physics-backed MuJoCo station using the official AGIBot X2 Ultra model."""

from __future__ import annotations

import mujoco
import numpy as np

from .model import model_assets, resolve_model_dir
from .policy import INSPECTION_INDICES


MODEL_COMMIT = "77f43eb0904dae4c48ccd9154fee824f8ffd4d38"


def _station_xml() -> str:
    return """<mujoco model="robopay_agibot_x2_inspection_station">
      <include file="X2-Ultra.xml"/>
      <option timestep="0.002" gravity="0 0 -9.81"/>
      <visual><global azimuth="145" elevation="-12"/><headlight ambient="0.45 0.45 0.45" diffuse="0.75 0.75 0.75"/></visual>
      <asset><texture name="floor_grid" type="2d" builtin="checker" rgb1="0.16 0.18 0.22" rgb2="0.24 0.27 0.32" width="512" height="512"/><material name="floor" texture="floor_grid" texrepeat="4 4" reflectance="0.12"/></asset>
      <worldbody>
        <light pos="0 0 3" dir="0 0 -1" directional="true"/>
        <geom name="floor" type="plane" size="2.5 2.5 0.1" material="floor" friction="1 0.01 0.001"/>
        <body name="target_left" pos="0.85 0.72 1.12"><geom type="sphere" size="0.075" contype="0" conaffinity="0" rgba="0.95 0.18 0.12 1"/></body>
        <body name="target_center" pos="1.05 0 1.18"><geom type="sphere" size="0.075" contype="0" conaffinity="0" rgba="0.12 0.88 0.30 1"/></body>
        <body name="target_right" pos="0.85 -0.72 1.12"><geom type="sphere" size="0.075" contype="0" conaffinity="0" rgba="0.12 0.38 0.98 1"/></body>
        <body name="support_post" pos="-0.12 0 0.39"><geom type="box" size="0.035 0.045 0.39" rgba="0.20 0.22 0.26 1"/></body>
      </worldbody>
      <equality><weld name="inspection_support" body1="pelvis"/></equality>
    </mujoco>"""


class X2InspectionEnvironment:
    def __init__(self, model_dir: str | None = None):
        directory = resolve_model_dir(model_dir)
        self.model = mujoco.MjModel.from_xml_string(_station_xml(), assets=model_assets(directory))
        self.data = mujoco.MjData(self.model)
        self.qpos_addresses = np.array([self.model.jnt_qposadr[self.model.actuator_trnid[i, 0]] for i in range(self.model.nu)])
        self.dof_addresses = np.array([self.model.jnt_dofadr[self.model.actuator_trnid[i, 0]] for i in range(self.model.nu)])
        self.pelvis_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        self.left_hand_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "left_wrist_roll_link")
        self.right_hand_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "right_wrist_roll_link")
        self.head_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "head_pitch_link")
        self.initial_hands: tuple[np.ndarray, np.ndarray] | None = None
        self.max_joint_speed = 0.0
        self.max_hand_motion = [0.0, 0.0]

    def reset(self) -> dict:
        mujoco.mj_resetData(self.model, self.data)
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
            "inspection_joint_positions": positions[INSPECTION_INDICES],
            "pelvis_position": self.data.xpos[self.pelvis_id].copy(),
            "head_position": self.data.xpos[self.head_id].copy(),
            "left_hand_position": self.data.xpos[self.left_hand_id].copy(),
            "right_hand_position": self.data.xpos[self.right_hand_id].copy(),
        }

    def step(self, torque: np.ndarray) -> dict:
        if torque.shape != (31,):
            raise ValueError(f"Expected 31 motor torques, got {torque.shape}.")
        # The official MJCF exposes direct-drive torque motors. Compensate the
        # model-derived gravity/Coriolis bias, then apply the policy PD term.
        command = torque + self.data.qfrc_bias[self.dof_addresses]
        self.data.ctrl[:] = np.clip(command, self.model.actuator_ctrlrange[:, 0], self.model.actuator_ctrlrange[:, 1])
        mujoco.mj_step(self.model, self.data)
        self.max_joint_speed = max(self.max_joint_speed, float(np.max(np.abs(self.data.qvel[self.dof_addresses]))))
        if self.initial_hands is not None:
            self.max_hand_motion[0] = max(self.max_hand_motion[0], float(np.linalg.norm(self.data.xpos[self.left_hand_id] - self.initial_hands[0])))
            self.max_hand_motion[1] = max(self.max_hand_motion[1], float(np.linalg.norm(self.data.xpos[self.right_hand_id] - self.initial_hands[1])))
        return self.observe()

    def safe_stop(self, torque: np.ndarray) -> dict:
        self.data.ctrl[:] = np.clip(torque, self.model.actuator_ctrlrange[:, 0], self.model.actuator_ctrlrange[:, 1])
        self.data.qvel[self.dof_addresses] = 0.0
        mujoco.mj_forward(self.model, self.data)
        return self.observe()
