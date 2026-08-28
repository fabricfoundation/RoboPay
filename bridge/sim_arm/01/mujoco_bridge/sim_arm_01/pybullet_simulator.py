"""PyBullet implementation of the sim-arm-01 2-DOF planar arm, for sim-to-sim.

Exposes the same execute(target_qpos) -> metrics interface as the MuJoCo
SimArm01Simulator, driven by the same closed-loop position-servo logic, so the
skill can be validated on a second, independent physics engine. PyBullet uses
radians natively, so there is no angle-units trap here.
"""
import numpy as np
import pybullet as p

LINK1_LEN = 0.25
LINK2_LEN = 0.20
MAX_STEPS = 1200
SETTLE_VEL = 0.05
SUCCESS_THRESHOLD = 0.03   # radians (joint-space error) — matches MuJoCo


class SimArm01PyBullet:
    """Closed-loop position-servo controller on a PyBullet 2-DOF arm."""

    def __init__(self):
        self._client = p.connect(p.DIRECT)      # headless
        self._build_arm()

    def _build_arm(self):
        p.resetSimulation(physicsClientId=self._client)
        p.setGravity(0, 0, -9.81, physicsClientId=self._client)
        p.setTimeStep(0.005, physicsClientId=self._client)   # matches MuJoCo

        col1 = p.createCollisionShape(p.GEOM_CAPSULE, radius=0.04, height=LINK1_LEN,
                                      physicsClientId=self._client)
        col2 = p.createCollisionShape(p.GEOM_CAPSULE, radius=0.03, height=LINK2_LEN,
                                      physicsClientId=self._client)
        self._body = p.createMultiBody(
            baseMass=0, baseCollisionShapeIndex=-1, basePosition=[0, 0, 0],
            linkMasses=[0.5, 0.4],
            linkCollisionShapeIndices=[col1, col2],
            linkVisualShapeIndices=[-1, -1],
            linkPositions=[[0, 0, 0], [LINK1_LEN, 0, 0]],
            linkOrientations=[[0, 0, 0, 1], [0, 0, 0, 1]],
            linkInertialFramePositions=[[LINK1_LEN / 2, 0, 0], [LINK2_LEN / 2, 0, 0]],
            linkInertialFrameOrientations=[[0, 0, 0, 1], [0, 0, 0, 1]],
            linkParentIndices=[0, 1],
            linkJointTypes=[p.JOINT_REVOLUTE, p.JOINT_REVOLUTE],
            linkJointAxis=[[0, 0, 1], [0, 0, 1]],
            physicsClientId=self._client)
        for j in range(2):
            p.changeDynamics(self._body, j, jointDamping=3.0,
                             jointLowerLimit=-3.14, jointUpperLimit=3.14,
                             physicsClientId=self._client)

    def execute(self, target_qpos: list) -> dict:
        target = np.array(target_qpos, dtype=float)
        for j in range(2):
            p.resetJointState(self._body, j, targetValue=0.0, targetVelocity=0.0,
                              physicsClientId=self._client)

        ctrl = np.clip(target, -3.14, 3.14)      # actuator enforces joint limits
        steps = 0
        for steps in range(MAX_STEPS):
            for j in range(2):
                p.setJointMotorControl2(
                    self._body, j, p.POSITION_CONTROL, targetPosition=float(ctrl[j]),
                    positionGain=0.3, force=50.0, physicsClientId=self._client)
            p.stepSimulation(physicsClientId=self._client)
            qpos, qvel = self._joint_state()
            error = float(np.linalg.norm(qpos - target))
            if error < SUCCESS_THRESHOLD and np.max(np.abs(qvel)) < SETTLE_VEL:
                break

        qpos, qvel = self._joint_state()
        error = float(np.linalg.norm(qpos - target))
        contacts = p.getContactPoints(bodyA=self._body, physicsClientId=self._client)
        return {
            "joint_angles": qpos.tolist(),
            "joint_velocities": qvel.tolist(),
            "joint_error": round(error, 4),
            "success": error < SUCCESS_THRESHOLD,
            "collision": len(contacts) > 0,
            "steps_taken": steps + 1,
        }

    def _joint_state(self):
        qpos, qvel = [], []
        for j in range(2):
            pos, vel, _, _ = p.getJointState(self._body, j,
                                             physicsClientId=self._client)
            qpos.append(pos)
            qvel.append(vel)
        return np.array(qpos), np.array(qvel)

    def close(self):
        p.disconnect(physicsClientId=self._client)
