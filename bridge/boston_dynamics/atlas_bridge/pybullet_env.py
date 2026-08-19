"""PyBullet environment for the Atlas shelf-inspection task.

This is the sim-to-sim counterpart of :mod:`mujoco_env`.  It loads the *same*
pinned Atlas v4 URDF, builds the *same* shelf from :mod:`task`, and is driven by
the *same* :class:`~.control_core.ShelfInspectionController`, so a metric
difference between the two runs is a physics-engine difference and nothing else.
"""

from __future__ import annotations

import math

import numpy as np
import pybullet
import pybullet_data

from .model import physics_urdf
from .task import (
    END_EFFECTOR_BODY,
    FALL_THRESHOLD_M,
    INSPECTION_CHAIN,
    SHELF_PARTS,
    STANCE_POSE,
)

#: Matches ``mujoco_env`` so both engines integrate at the same rate.
TIME_STEP_S = 1.0 / 500.0
SPAWN_HEIGHT_M = 1.20
SPAWN_CLEARANCE_M = 0.002


class AtlasInspectionPyBulletEnvironment:
    """Free-standing Atlas v4 in PyBullet, same task geometry as MuJoCo."""

    def __init__(self, gui: bool = False) -> None:
        self.client = pybullet.connect(pybullet.GUI if gui else pybullet.DIRECT)
        pybullet.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=self.client)
        pybullet.setGravity(0, 0, -9.81, physicsClientId=self.client)
        pybullet.setTimeStep(TIME_STEP_S, physicsClientId=self.client)
        pybullet.loadURDF("plane.urdf", physicsClientId=self.client)

        self.robot = pybullet.loadURDF(
            str(physics_urdf()), [0, 0, SPAWN_HEIGHT_M], useFixedBase=False,
            physicsClientId=self.client,
        )
        self.joint_indices: dict[str, int] = {}
        self.link_indices: dict[str, int] = {}
        for index in range(pybullet.getNumJoints(self.robot, physicsClientId=self.client)):
            info = pybullet.getJointInfo(self.robot, index, physicsClientId=self.client)
            self.link_indices[info[12].decode()] = index
            if info[2] != pybullet.JOINT_FIXED:
                self.joint_indices[info[1].decode()] = index
        self.names = tuple(self.joint_indices)
        self.effort_limits = np.array(
            [
                pybullet.getJointInfo(self.robot, self.joint_indices[name],
                                      physicsClientId=self.client)[10]
                for name in self.names
            ],
            dtype=np.float64,
        )
        self.hand_index = self.link_indices[END_EFFECTOR_BODY]
        self._chain_slots = [self.names.index(joint) for joint in INSPECTION_CHAIN]

        # Release the implicit velocity motors PyBullet attaches to every joint,
        # so the only thing driving Atlas is this bridge's own servo command.
        for index in self.joint_indices.values():
            pybullet.setJointMotorControl2(
                self.robot, index, pybullet.VELOCITY_CONTROL, force=0.0,
                physicsClientId=self.client,
            )

        self.shelf_ids: list[int] = []
        for part in SHELF_PARTS:
            shape = pybullet.createCollisionShape(
                pybullet.GEOM_BOX, halfExtents=list(part["half"]),
                physicsClientId=self.client,
            )
            self.shelf_ids.append(
                pybullet.createMultiBody(
                    baseMass=0.0, baseCollisionShapeIndex=shape,
                    basePosition=list(part["pos"]), physicsClientId=self.client,
                )
            )

        self.min_pelvis_height = math.inf
        self.max_end_effector_speed = 0.0
        self.shelf_contacts = 0
        self.fall_detected = False
        self._time = 0.0

    # -- episode -----------------------------------------------------------
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        limits: dict[str, tuple[float, float]] = {}
        for name, index in self.joint_indices.items():
            info = pybullet.getJointInfo(self.robot, index, physicsClientId=self.client)
            low, high = info[8], info[9]
            limits[name] = (low, high) if low < high else (-math.pi, math.pi)
        return limits

    def _vector(self, targets: dict[str, float]) -> np.ndarray:
        return np.array([targets.get(name, 0.0) for name in self.names], dtype=np.float64)

    def reset(self, joint_targets: dict[str, float]) -> dict:
        pose = self._vector({**STANCE_POSE, **joint_targets})
        pybullet.resetBasePositionAndOrientation(
            self.robot, [0, 0, SPAWN_HEIGHT_M], [0, 0, 0, 1], physicsClientId=self.client
        )
        for value, name in zip(pose, self.names):
            pybullet.resetJointState(
                self.robot, self.joint_indices[name], float(value), 0.0,
                physicsClientId=self.client,
            )
        lowest = min(
            pybullet.getAABB(self.robot, index, physicsClientId=self.client)[0][2]
            for index in range(-1, pybullet.getNumJoints(self.robot, physicsClientId=self.client))
        )
        pybullet.resetBasePositionAndOrientation(
            self.robot,
            [0, 0, SPAWN_HEIGHT_M - lowest + SPAWN_CLEARANCE_M],
            [0, 0, 0, 1],
            physicsClientId=self.client,
        )
        self.min_pelvis_height = self._pelvis_height()
        self.max_end_effector_speed = 0.0
        self.shelf_contacts = 0
        self.fall_detected = False
        self._time = 0.0
        return self.observe()

    def step(self, joint_targets: dict[str, float]) -> dict:
        """Servo towards ``joint_targets`` and advance one physics step.

        MuJoCo runs an explicit PD law with a gravity feedforward; PyBullet uses
        its own implicit joint servo, saturated at the same URDF effort limits.
        An explicit PD at these gains is numerically unstable at PyBullet's fixed
        step, so the *servo implementation* differs by engine while the task, the
        robot, the state machine and the IK stay identical — that difference is
        exactly what the sim-to-sim comparison is there to bound.
        """
        desired = self._vector(joint_targets)
        pybullet.setJointMotorControlArray(
            self.robot,
            list(self.joint_indices.values()),
            pybullet.POSITION_CONTROL,
            targetPositions=desired.tolist(),
            forces=self.effort_limits.tolist(),
            physicsClientId=self.client,
        )
        pybullet.stepSimulation(physicsClientId=self.client)
        self._time += TIME_STEP_S
        return self.observe()

    def safe_stop(self) -> dict:
        pybullet.setJointMotorControlArray(
            self.robot, list(self.joint_indices.values()), pybullet.TORQUE_CONTROL,
            forces=[0.0] * len(self.names), physicsClientId=self.client,
        )
        return self.observe()

    # -- measurement -------------------------------------------------------
    def _pelvis_height(self) -> float:
        return float(
            pybullet.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)[0][2]
        )

    def end_effector(self) -> np.ndarray:
        state = pybullet.getLinkState(
            self.robot, self.hand_index, computeLinkVelocity=1,
            computeForwardKinematics=1, physicsClientId=self.client,
        )
        return np.array(state[0], dtype=np.float64)

    def joint_angles(self) -> dict[str, float]:
        """Measured joint positions, keyed by Atlas joint name."""
        states = pybullet.getJointStates(
            self.robot, list(self.joint_indices.values()), physicsClientId=self.client
        )
        return {name: float(state[0]) for name, state in zip(self.names, states)}

    def base_rotation(self) -> np.ndarray:
        _, orientation = pybullet.getBasePositionAndOrientation(
            self.robot, physicsClientId=self.client
        )
        return np.array(
            pybullet.getMatrixFromQuaternion(orientation), dtype=np.float64
        ).reshape(3, 3)

    def observe(self) -> dict:
        position, orientation = pybullet.getBasePositionAndOrientation(
            self.robot, physicsClientId=self.client
        )
        height = float(position[2])
        self.min_pelvis_height = min(self.min_pelvis_height, height)
        if height < FALL_THRESHOLD_M:
            self.fall_detected = True

        contacts = 0
        for shelf in self.shelf_ids:
            contacts += len(
                pybullet.getContactPoints(
                    bodyA=self.robot, bodyB=shelf, physicsClientId=self.client
                )
            )
        self.shelf_contacts += contacts

        velocity = pybullet.getLinkState(
            self.robot, self.hand_index, computeLinkVelocity=1, physicsClientId=self.client
        )[6]
        speed = float(np.linalg.norm(velocity))
        self.max_end_effector_speed = max(self.max_end_effector_speed, speed)
        roll, pitch, _ = pybullet.getEulerFromQuaternion(orientation)

        return {
            "sim_time": self._time,
            "pelvis_height": height,
            "pelvis_position": np.array(position, dtype=np.float64),
            "torso_roll": float(roll),
            "torso_pitch": float(pitch),
            "end_effector": self.end_effector(),
            "end_effector_speed": speed,
            "shelf_contacts_step": contacts,
            "upright": height >= FALL_THRESHOLD_M,
        }

    def close(self) -> None:
        pybullet.disconnect(physicsClientId=self.client)
