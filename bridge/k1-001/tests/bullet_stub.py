"""A minimal stand-in for the `pybullet` module.

Purpose: exercise every PyBullet call the K1 backend makes -- names, keyword
arguments, return-tuple indices -- on machines where the real wheel cannot be
built (PyBullet is source-only and needs a compiler on Windows).

This is a CONTRACT check, not a physics check. It deliberately does not model
dynamics; it parses the backend's own URDF for the joint ordering and returns
plausible sensor tuples so the control flow can be walked end to end. The real
physics agreement is asserted by TestSimToSimAgreement, which runs on CI where
PyBullet is importable.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import arm_spec

DIRECT = 2
GEOM_PLANE = 3
GEOM_BOX = 4
GEOM_CYLINDER = 5
VELOCITY_CONTROL = 6
JOINT_POINT2POINT = 7


class _State:
    def __init__(self):
        self.reset()

    def reset(self):
        self.next_id = 100
        self.joint_names = []
        self.joints = {}
        self.robot = None
        self.targets = {}          # body id -> base position (inspection targets)
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


def createMultiBody(baseMass, baseCollisionShapeIndex=-1,
                    baseVisualShapeIndex=-1, basePosition=(0, 0, 0),
                    physicsClientId=0, **kw):
    _log("createMultiBody")
    bid = _new_id()
    # A massless body with a non-zero base position is an inspection target
    # (the K1 backend creates each target as a static cylinder).
    if baseMass == 0 and basePosition != (0, 0, 0) \
            and list(basePosition) != [0, 0, 0]:
        S.targets[bid] = list(basePosition)
    return bid


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
    S.robot = _new_id()
    return S.robot


def getNumJoints(bodyUniqueId, physicsClientId=0):
    _log("getNumJoints")
    return len(S.joint_names)


def getJointInfo(bodyUniqueId, jointIndex, physicsClientId=0):
    _log("getJointInfo")
    name = S.joint_names[jointIndex].encode()
    return (jointIndex, name, 0, -1, -1, 0, 0.0, 0.0,
            -3.15, 3.15, 200.0, 10.0, b"link", (0, 0, 1), (0, 0, 0),
            (0, 0, 0, 1), -1)


def setJointMotorControl2(bodyUniqueId, jointIndex, controlMode,
                          physicsClientId=0, **kw):
    _log("setJointMotorControl2")


def resetJointState(bodyUniqueId, jointIndex, targetValue,
                    targetVelocity=0.0, physicsClientId=0):
    _log("resetJointState")
    S.joints[jointIndex] = targetValue


def stepSimulation(physicsClientId=0):
    _log("stepSimulation")
    S.steps += 1


# ------------------------------------------------------------------ sensing
def _pose():
    idx = {n: i for i, n in enumerate(S.joint_names)}
    return {j: S.joints[idx[j]] for j in arm_spec.ARM_JOINTS}


def getContactPoints(bodyA=None, bodyB=None, physicsClientId=0):
    # The K1 inspection arm has no gripper; no contact sensing is expected.
    return []


def getBasePositionAndOrientation(bodyUniqueId, physicsClientId=0):
    _log("getBasePositionAndOrientation")
    if bodyUniqueId in S.targets:
        return tuple(S.targets[bodyUniqueId]), (0.0, 0.0, 0.0, 1.0)
    return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)


def getLinkState(bodyUniqueId, linkIndex, computeForwardKinematics=False,
                 physicsClientId=0):
    _log("getLinkState")
    # The camera rides on the cam_mount link; its world position comes from
    # the same arm_spec.forward() FK the MuJoCo backend uses.
    x, y, z = arm_spec.forward(_pose())
    frame = (x, y, z)
    orn = (0.0, 0.0, 0.0, 1.0)
    return (frame, orn, (0, 0, 0), (0, 0, 0, 1), frame, orn)


def getMatrixFromQuaternion(orn, physicsClientId=0):
    return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


# -------------------------------------------------------------- constraints
def createConstraint(parentBodyUniqueId, parentLinkIndex, childBodyUniqueId,
                     childLinkIndex, jointType, jointAxis,
                     parentFramePosition, childFramePosition,
                     physicsClientId=0, **kw):
    _log("createConstraint")
    return _new_id()


def changeConstraint(userConstraintUniqueId, physicsClientId=0, **kw):
    _log("changeConstraint")


def removeConstraint(userConstraintUniqueId, physicsClientId=0):
    _log("removeConstraint")