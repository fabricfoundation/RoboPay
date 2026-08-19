"""MuJoCo environment for the Atlas shelf-inspection task."""

from __future__ import annotations

import math

import mujoco
import numpy as np

from . import actuators as actuator_map
from . import kinematics
from .model import SPAWN_HEIGHT_M, joint_efforts, scene_xml
from .task import (
    END_EFFECTOR_BODY,
    FALL_THRESHOLD_M,
    INSPECTION_CHAIN,
    INSPECTION_TARGETS,
    SERVO_KD,
    SERVO_KP,
    SHELF_PARTS,
    STANCE_POSE,
)

#: Clearance added after dropping the robot so the soles start just off the floor.
SPAWN_CLEARANCE_M = 0.002


def _quaternion_to_rpy(quat: np.ndarray) -> tuple[float, float]:
    w, x, y, z = quat
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    return roll, pitch


class AtlasInspectionEnvironment:
    """Free-standing Atlas v4 in front of an inspection shelf.

    The robot is never welded, clamped or otherwise held up: it stands on its own
    soles for the whole episode and the fall check in :attr:`fall_detected` uses
    the real standing height, not floor contact.
    """

    def __init__(self, show_targets: bool = False) -> None:
        """``show_targets`` adds non-colliding markers used only by the renderer.

        The markers carry ``contype=0`` and ``conaffinity=0`` and sit on bodies
        with no joint, so they cannot influence the simulation.  The evidence
        renderer asserts that an episode with markers produces exactly the same
        metrics as one without.
        """
        spec = mujoco.MjSpec.from_string(scene_xml(free_base=True))
        for part in SHELF_PARTS:
            body = spec.worldbody.add_body(name=part["name"], pos=list(part["pos"]))
            body.add_geom(
                name=f"{part['name']}_geom",
                type=mujoco.mjtGeom.mjGEOM_BOX,
                size=list(part["half"]),
                rgba=[0.55, 0.42, 0.28, 1.0],
            )
        if show_targets:
            for target in INSPECTION_TARGETS:
                marker = spec.worldbody.add_body(
                    name=f"marker_{target.name}", pos=list(target.position)
                )
                marker.add_geom(
                    name=f"marker_{target.name}_geom",
                    type=mujoco.mjtGeom.mjGEOM_SPHERE,
                    size=[target.tolerance_m, 0.0, 0.0],
                    rgba=[0.10, 0.85, 0.45, 0.35],
                    contype=0,
                    conaffinity=0,
                )

        self.model = spec.compile()
        self.data = mujoco.MjData(self.model)

        self.actuators = actuator_map.validate(self.model, joint_efforts())
        self.pelvis_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        self.hand_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, END_EFFECTOR_BODY
        )
        self._chain_dofs = [
            int(self.model.jnt_dofadr[self.model.actuator_trnid[self.actuators.index(joint), 0]])
            for joint in INSPECTION_CHAIN
        ]
        self._shelf_geoms = {
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, f"{part['name']}_geom")
            for part in SHELF_PARTS
        }
        self._floor_geom = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        self._jacp = np.zeros((3, self.model.nv))

        self.min_pelvis_height = math.inf
        self.max_end_effector_speed = 0.0
        self.shelf_contacts = 0
        self.fall_detected = False

    # -- episode -----------------------------------------------------------
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        limits: dict[str, tuple[float, float]] = {}
        for name in self.actuators.names:
            joint = self.model.joint(name)
            low, high = (float(joint.range[0]), float(joint.range[1]))
            limits[name] = (low, high) if joint.limited[0] else (-math.pi, math.pi)
        return limits

    def reset(self, joint_targets: dict[str, float]) -> dict:
        """Place Atlas standing on its soles in the requested pose."""
        mujoco.mj_resetData(self.model, self.data)
        pose = self.actuators.vector({**STANCE_POSE, **joint_targets})
        self.data.qpos[3] = 1.0
        self.data.qpos[self.actuators.qpos_addresses] = pose
        self.data.qpos[2] = SPAWN_HEIGHT_M
        mujoco.mj_forward(self.model, self.data)
        self.data.qpos[2] = SPAWN_HEIGHT_M - self._lowest_point() + SPAWN_CLEARANCE_M
        mujoco.mj_forward(self.model, self.data)

        self.min_pelvis_height = float(self.data.xpos[self.pelvis_id][2])
        self.max_end_effector_speed = 0.0
        self.shelf_contacts = 0
        self.fall_detected = False
        return self.observe()

    def _lowest_point(self) -> float:
        return min(
            float(self.data.geom_xpos[i][2] - self.model.geom_size[i][2])
            for i in range(self.model.ngeom)
            if self.model.geom_bodyid[i] != 0
            and self.model.geom_type[i] == mujoco.mjtGeom.mjGEOM_BOX
        )

    def step(self, joint_targets: dict[str, float]) -> dict:
        """Apply one servo step towards ``joint_targets`` and advance physics."""
        desired = self.actuators.vector(joint_targets)
        positions = self.data.qpos[self.actuators.qpos_addresses]
        velocities = self.data.qvel[self.actuators.qvel_addresses]
        # Gravity feedforward removes the steady-state droop a pure PD servo
        # leaves under load; without it Atlas slowly yields at the ankles while
        # holding an extended arm and eventually topples.  The term comes from
        # the shared URDF model rather than from MuJoCo, so every engine runs the
        # same maths; test_gravity_model_matches_mujoco pins the two together.
        gravity = self.actuators.vector(
            kinematics.gravity_torques(self.joint_angles(), self.base_rotation())
        )
        command = SERVO_KP * (desired - positions) - SERVO_KD * velocities + gravity
        self.data.ctrl[:] = np.clip(command / self.actuators.effort_limits, -1.0, 1.0)
        mujoco.mj_step(self.model, self.data)
        return self.observe()

    def safe_stop(self) -> dict:
        """Zero every actuator and freeze the robot, as required by the tunnel."""
        self.data.ctrl[:] = 0.0
        self.data.qvel[:] = 0.0
        mujoco.mj_forward(self.model, self.data)
        return self.observe()

    # -- measurement -------------------------------------------------------
    def end_effector(self) -> np.ndarray:
        return self.data.xpos[self.hand_id].copy()

    def joint_angles(self) -> dict[str, float]:
        """Measured joint positions, keyed by Atlas joint name."""
        return {
            name: float(self.data.qpos[address])
            for name, address in zip(self.actuators.names, self.actuators.qpos_addresses)
        }

    def base_rotation(self) -> np.ndarray:
        """Pelvis orientation, used to lift the arm Jacobian into world frame."""
        return self.data.xmat[self.pelvis_id].reshape(3, 3).copy()

    def engine_jacobian(self) -> np.ndarray:
        """MuJoCo's own Jacobian, kept only so tests can cross-check ours."""
        mujoco.mj_jacBody(self.model, self.data, self._jacp, None, self.hand_id)
        return self._jacp[:, self._chain_dofs].copy()

    def observe(self) -> dict:
        pelvis_height = float(self.data.xpos[self.pelvis_id][2])
        self.min_pelvis_height = min(self.min_pelvis_height, pelvis_height)
        if pelvis_height < FALL_THRESHOLD_M:
            self.fall_detected = True

        contacts = 0
        for i in range(self.data.ncon):
            pair = {self.data.contact[i].geom1, self.data.contact[i].geom2}
            if pair & self._shelf_geoms:
                contacts += 1
        self.shelf_contacts += contacts

        speed = float(np.linalg.norm(self.data.cvel[self.hand_id][:3]))
        self.max_end_effector_speed = max(self.max_end_effector_speed, speed)
        roll, pitch = _quaternion_to_rpy(self.data.qpos[3:7])

        return {
            "sim_time": float(self.data.time),
            "pelvis_height": pelvis_height,
            "pelvis_position": self.data.xpos[self.pelvis_id].copy(),
            "torso_roll": roll,
            "torso_pitch": pitch,
            "end_effector": self.end_effector(),
            "end_effector_speed": speed,
            "shelf_contacts_step": contacts,
            "upright": pelvis_height >= FALL_THRESHOLD_M,
        }
