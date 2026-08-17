"""A minimal stand-in for the `pybullet` module (planar biped unitree-g1).

Purpose: exercise every PyBullet call the backend makes -- names, keyword
arguments, return-tuple indices -- on machines where the real wheel cannot be
built (PyBullet is source-only and needs a compiler on Windows).

This is a CONTRACT check, not a physics check. It deliberately does not model
dynamics; it parses the backend's own URDF for the joint ordering and follows
the position-control targets the backend issues, so the control flow can be
walked end to end. The real physics agreement is asserted by
TestSimToSimAgreement, which runs on CI where PyBullet is importable.

The planar biped has six joints: torso_x (prismatic X), torso_pitch (revolute
-- the inverted-pendulum hinge about the hip line) plus the four leg hinges
(left_hip / left_knee / right_hip / right_knee). There is no gripper.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import g1_spec

DIRECT = 2
GEOM_PLANE = 3
GEOM_BOX = 4
GEOM_CYLINDER = 5
POSITION_CONTROL = 1
VELOCITY_CONTROL = 6
TORQUE_CONTROL = 3
JOINT_POINT2POINT = 7


class _State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.next_id = 100
        self.joint_names = []
        self.joint_targets = {}     # jointIndex -> last POSITION_CONTROL target
        self.joints = {}            # jointIndex -> simulated position
        self.robot = None
        self.steps = 0
        self.calls = []


S = _State()


def _new_id():
    S.next_id += 1
    return S.next_id


def _log(name):
    S.calls.append(name)


# ------------------------------------------------------------------ session
def connect(mode, **kw):
    _log("connect")
    S.reset()
    return 0


def disconnect(physicsClientId=0):
    _log("disconnect")


def setGravity(x, y, z, physicsClientId=0):
    _log("setGravity")


def setTimeStep(dt, physicsClientId=0):
    _log("setTimeStep")


def setPhysicsEngineParameter(physicsClientId=0, **kw):
    _log("setPhysicsEngineParameter")


# ------------------------------------------------------------------- shapes
def createCollisionShape(shapeType, physicsClientId=0, **kw):
    _log("createCollisionShape")
    return _new_id()


def createVisualShape(shapeType, physicsClientId=0, **kw):
    _log("createVisualShape")
    return _new_id()


def createMultiBody(baseMass=0, baseCollisionShapeIndex=-1,
                    baseVisualShapeIndex=-1, basePosition=(0, 0, 0),
                    physicsClientId=0, **kw):
    _log("createMultiBody")
    return _new_id()


def changeDynamics(bodyUniqueId, linkIndex, physicsClientId=0, **kw):
    _log("changeDynamics")


def setCollisionFilterGroupMask(bodyUniqueId, linkIndexA, collisionFilterGroup,
                                collisionFilterMask, physicsClientId=0):
    _log("setCollisionFilterGroupMask")


# -------------------------------------------------------------------- robot
def loadURDF(path, basePosition=(0, 0, 0), useFixedBase=False,
             physicsClientId=0, **kw):
    """Parse the real URDF so joint ordering comes from the backend itself."""
    _log("loadURDF")
    root = ET.parse(path).getroot()
    S.joint_names = [j.get("name") for j in root.findall("joint")]
    S.joints = {i: 0.0 for i in range(len(S.joint_names))}
    S.joint_targets = {}
    S.robot = _new_id()
    return S.robot


def getNumJoints(bodyUniqueId, physicsClientId=0):
    return len(S.joint_names)


def getJointInfo(bodyUniqueId, jointIndex, physicsClientId=0):
    name = S.joint_names[jointIndex].encode()
    return (jointIndex, name, 0, -1, -1, 0, 0.0, 0.0,
            -3.15, 3.15, 200.0, 10.0, b"link", (0, 0, 1), (0, 0, 0),
            (0, 0, 0, 1), -1)


def setJointMotorControl2(bodyUniqueId, jointIndex, controlMode,
                          physicsClientId=0, **kw):
    _log("setJointMotorControl2")
    if "targetPosition" in kw:
        S.joint_targets[jointIndex] = float(kw["targetPosition"])


def resetJointState(bodyUniqueId, jointIndex, targetValue,
                    targetVelocity=0.0, physicsClientId=0):
    S.joints[jointIndex] = float(targetValue)


def stepSimulation(physicsClientId=0):
    _log("stepSimulation")
    S.steps += 1
    # Follow the last position-control target for every joint (instant
    # servo). This makes the torso X track the backend's walk trajectory so
    # the same success / timeout verdicts the real engine produces appear
    # here too -- enough to walk the control flow deterministically.
    for idx, target in S.joint_targets.items():
        S.joints[idx] = target


def getJointState(bodyUniqueId, jointIndex, physicsClientId=0):
    return (float(S.joints.get(jointIndex, 0.0)), 0.0)


def getBasePositionAndOrientation(bodyUniqueId, physicsClientId=0):
    return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
