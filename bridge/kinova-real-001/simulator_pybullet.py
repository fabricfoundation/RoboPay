"""kinova-real-001 --- PyBullet backend (sim-to-sim cross-check).

Same robot, same skill, same trajectory, different physics engine.

Everything that defines the robot and the skill -- link lengths, gripper
geometry, keyframes, stage step counts, force/lift thresholds, scene layout --
is imported from arm_spec.py, exactly as the MuJoCo backend does. The only
thing that differs below is how the world is assembled and stepped. That is
what makes the sim-to-sim test meaningful: if both engines agree on
success/failure, grasp state and lift distance, the skill is a property of the
robot definition, not of one simulator's quirks.

PyBullet ships as a source distribution only, so it builds on Linux CI but
usually not on a bare Windows box. Import is lazy and every consumer is
expected to skip when `available()` is False.

Public surface (identical to simulator.MuJoCoSimulator):
    PyBulletSimulator().pick_object(params) -> PickResult
"""
from __future__ import annotations

import math
import os
import tempfile
import time

from arm_spec import (
    ARM_JOINTS, BASE_H, BudgetExhausted, CUBE_FRICTION, CUBE_HALF, CUBE_MASS,
    FINGER_CLOSED, FINGER_HALF_X, FINGER_HALF_Z, FINGER_OPEN, GRASP_FORCE_MIN,
    GRIP_MID, KEYFRAMES, LIFT_MIN, LINK1, LINK2, OBSTACLE_HALF_H,
    OBSTACLE_RADIUS, PAD_HALF, PickResult, STAGE_STEPS, TIMESTEP,
    UNREACHABLE_GAP, WORK_R, aperture_at, blend, build_metrics, resolve_scene,
)

ENGINE = "pybullet"

# collision groups mirror the MJCF bitmasks in simulator.py
G_FLOOR, M_FLOOR = 1, 6
G_CUBE, M_CUBE = 2, 13
G_PAD, M_PAD = 4, 11
G_OBSTACLE, M_OBSTACLE = 8, 22
G_ARM, M_ARM = 16, 8

_GRIP_JOINTS = ("grip_l", "grip_r")


def available() -> bool:
    """True when the PyBullet wheel is importable in this environment."""
    try:
        import pybullet  # noqa: F401
    except Exception:
        return False
    return True


# --------------------------------------------------------------------- URDF --
def _inertial(mass: float) -> str:
    i = max(1e-5, mass * 0.01)
    return (f'<inertial><mass value="{mass}"/>'
            f'<inertia ixx="{i}" ixy="0" ixz="0" iyy="{i}" iyz="0" izz="{i}"/>'
            f'</inertial>')


def _cyl_link(name, length, radius, mass, rgba, along_x=False) -> str:
    """Capsule-ish link. URDF cylinders lie along +Z, so links that run along
    the arm's +X axis are rotated by pi/2 about +Y, matching the MJCF fromto."""
    rpy = "0 1.5707963 0" if along_x else "0 0 0"
    off = f'{length / 2} 0 0' if along_x else f'0 0 {length / 2}'
    geom = f'<cylinder length="{length}" radius="{radius}"/>'
    return f"""
  <link name="{name}">
    {_inertial(mass)}
    <visual><origin xyz="{off}" rpy="{rpy}"/><geometry>{geom}</geometry>
      <material name="{name}_m"><color rgba="{rgba}"/></material></visual>
    <collision><origin xyz="{off}" rpy="{rpy}"/><geometry>{geom}</geometry></collision>
  </link>"""


def _box_link(name, sx, sy, sz, mass, rgba) -> str:
    geom = f'<box size="{sx} {sy} {sz}"/>'
    return f"""
  <link name="{name}">
    {_inertial(mass)}
    <visual><geometry>{geom}</geometry>
      <material name="{name}_m"><color rgba="{rgba}"/></material></visual>
    <collision><geometry>{geom}</geometry></collision>
  </link>"""


def _joint(name, jtype, parent, child, xyz, axis, lo, hi) -> str:
    return f"""
  <joint name="{name}" type="{jtype}">
    <parent link="{parent}"/><child link="{child}"/>
    <origin xyz="{xyz}" rpy="0 0 0"/><axis xyz="{axis}"/>
    <limit lower="{lo}" upper="{hi}" effort="200" velocity="10"/>
  </joint>"""


def _robot_urdf() -> str:
    """The same kinematic chain the MJCF declares, in URDF form."""
    return f"""<?xml version="1.0"?>
<robot name="kinova-real-001">
  <link name="base">
    {_inertial(1.0)}
    <visual><origin xyz="0 0 0.025"/><geometry><cylinder length="0.05" radius="0.07"/></geometry>
      <material name="base_m"><color rgba="0.25 0.27 0.32 1"/></material></visual>
    <collision><origin xyz="0 0 0.025"/><geometry><cylinder length="0.05" radius="0.07"/></geometry></collision>
  </link>
{_cyl_link("column", 0.35, 0.035, 1.0, "0.30 0.32 0.38 1")}
{_cyl_link("upper", LINK1, 0.030, 0.8, "0.85 0.55 0.18 1", along_x=True)}
{_cyl_link("fore", LINK2, 0.026, 0.6, "0.85 0.55 0.18 1", along_x=True)}
{_box_link("wrist", 0.064, 0.060, 0.036, 0.3, "0.30 0.32 0.38 1")}
{_box_link("finger_l", 2 * FINGER_HALF_X, 2 * PAD_HALF, 2 * FINGER_HALF_Z, 0.05,
           "0.90 0.90 0.92 1")}
{_box_link("finger_r", 2 * FINGER_HALF_X, 2 * PAD_HALF, 2 * FINGER_HALF_Z, 0.05,
           "0.90 0.90 0.92 1")}
{_joint("pan", "revolute", "base", "column", "0 0 0.05", "0 0 1", -3.1416, 3.1416)}
{_joint("shoulder", "revolute", "column", "upper", "0 0 0.35", "0 1 0", -2.0, 2.0)}
{_joint("elbow", "revolute", "upper", "fore", f"{LINK1} 0 0", "0 1 0", -2.6, 2.6)}
{_joint("wristp", "revolute", "fore", "wrist", f"{LINK2} 0 0", "0 1 0", -2.8, 2.8)}
{_joint("grip_l", "prismatic", "wrist", "finger_l", f"0 0 -{GRIP_MID}", "0 1 0", 0.012, 0.060)}
{_joint("grip_r", "prismatic", "wrist", "finger_r", f"0 0 -{GRIP_MID}", "0 -1 0", 0.012, 0.060)}
</robot>
"""


# --------------------------------------------------------------- simulator --
class PyBulletSimulator:
    """Drop-in twin of MuJoCoSimulator running on Bullet."""

    ROBOT_ID = "kinova-real-001"
    SKILL_ID = "pick_object"
    ENGINE = ENGINE

    def __init__(self):
        if not available():                           # pragma: no cover
            raise RuntimeError("pybullet is not installed in this environment")
        import pybullet
        self._p = pybullet
        self._cid = None
        self._urdf_path = None

    # ---------------------------------------------------------- scene setup
    def _build(self, scene: dict):
        p = self._p
        self._teardown()
        self._cid = p.connect(p.DIRECT)
        c = self._cid
        p.setGravity(0, 0, -9.81, physicsClientId=c)
        p.setTimeStep(TIMESTEP, physicsClientId=c)
        p.setPhysicsEngineParameter(numSolverIterations=80, physicsClientId=c)

        # ground plane
        plane_shape = p.createCollisionShape(p.GEOM_PLANE, physicsClientId=c)
        self.floor = p.createMultiBody(0, plane_shape, physicsClientId=c)
        p.changeDynamics(self.floor, -1, lateralFriction=1.0, physicsClientId=c)
        p.setCollisionFilterGroupMask(self.floor, -1, G_FLOOR, M_FLOOR,
                                      physicsClientId=c)

        # robot
        fd, path = tempfile.mkstemp(suffix=".urdf", text=True)
        with os.fdopen(fd, "w") as fh:
            fh.write(_robot_urdf())
        self._urdf_path = path
        self.robot = p.loadURDF(path, [0, 0, 0], useFixedBase=True,
                                physicsClientId=c)

        self._jidx = {}
        for j in range(p.getNumJoints(self.robot, physicsClientId=c)):
            info = p.getJointInfo(self.robot, j, physicsClientId=c)
            self._jidx[info[1].decode()] = j
            # kinematic pinning: no motor should fight the scripted pose
            p.setJointMotorControl2(self.robot, j, p.VELOCITY_CONTROL,
                                    force=0, physicsClientId=c)
        self._pad_links = {self._jidx["grip_l"], self._jidx["grip_r"]}
        self._wrist_link = self._jidx["wristp"]

        for name, j in self._jidx.items():
            grp = G_PAD if name in _GRIP_JOINTS else G_ARM
            msk = M_PAD if name in _GRIP_JOINTS else M_ARM
            p.setCollisionFilterGroupMask(self.robot, j, grp, msk, physicsClientId=c)
            p.changeDynamics(self.robot, j, lateralFriction=CUBE_FRICTION,
                             contactStiffness=8000, contactDamping=80,
                             physicsClientId=c)
        p.setCollisionFilterGroupMask(self.robot, -1, G_ARM, M_ARM, physicsClientId=c)

        # payload
        cx, cy = scene["cube"]
        half = [CUBE_HALF] * 3
        cshape = p.createCollisionShape(p.GEOM_BOX, halfExtents=half, physicsClientId=c)
        vshape = p.createVisualShape(p.GEOM_BOX, halfExtents=half,
                                     rgbaColor=[0.20, 0.70, 0.45, 1],
                                     physicsClientId=c)
        self.cube = p.createMultiBody(CUBE_MASS, cshape, vshape,
                                      [cx, cy, CUBE_HALF], physicsClientId=c)
        p.changeDynamics(self.cube, -1, lateralFriction=CUBE_FRICTION,
                         contactStiffness=8000, contactDamping=80,
                         physicsClientId=c)
        p.setCollisionFilterGroupMask(self.cube, -1, G_CUBE, M_CUBE, physicsClientId=c)

        # obstacle
        self.obstacle = None
        if scene["obstacle"] is not None:
            ox, oy = scene["obstacle"]
            oshape = p.createCollisionShape(p.GEOM_CYLINDER, radius=OBSTACLE_RADIUS,
                                            height=2 * OBSTACLE_HALF_H,
                                            physicsClientId=c)
            ovis = p.createVisualShape(p.GEOM_CYLINDER, radius=OBSTACLE_RADIUS,
                                       length=2 * OBSTACLE_HALF_H,
                                       rgbaColor=[0.80, 0.25, 0.25, 1],
                                       physicsClientId=c)
            self.obstacle = p.createMultiBody(0, oshape, ovis,
                                              [ox, oy, OBSTACLE_HALF_H],
                                              physicsClientId=c)
            p.setCollisionFilterGroupMask(self.obstacle, -1, G_OBSTACLE,
                                          M_OBSTACLE, physicsClientId=c)

        self._pose = dict(KEYFRAMES["home"])
        self._grip = FINGER_OPEN
        self._steps = 0
        self._peak_force = 0.0
        self._hold_forces = []
        self._contact_samples = 0
        self._collisions = 0
        self._constraint = None
        self._apply(self._pose, self._grip)

    def _teardown(self):
        if self._cid is not None:
            try:
                self._p.disconnect(physicsClientId=self._cid)
            except Exception:                          # pragma: no cover
                pass
            self._cid = None
        if self._urdf_path and os.path.exists(self._urdf_path):
            try:
                os.unlink(self._urdf_path)
            except OSError:                            # pragma: no cover
                pass
            self._urdf_path = None

    def __del__(self):                                 # pragma: no cover
        self._teardown()

    # -------------------------------------------------- kinematic trajectory
    def _apply(self, pose: dict, grip: float):
        p, c = self._p, self._cid
        for name in ARM_JOINTS:
            p.resetJointState(self.robot, self._jidx[name], pose[name], 0.0,
                              physicsClientId=c)
        for name in _GRIP_JOINTS:
            p.resetJointState(self.robot, self._jidx[name], grip, 0.0,
                              physicsClientId=c)

    def _tick(self, pose: dict, grip: float):
        if self._steps >= self._budget:
            raise BudgetExhausted
        self._apply(pose, grip)
        self._p.stepSimulation(physicsClientId=self._cid)
        self._apply(pose, grip)          # re-pin after contact reaction
        self._steps += 1
        self._pose, self._grip = pose, grip
        if self.obstacle is not None and self._obstacle_contact():
            self._collisions += 1

    def _run(self, target: dict, n: int, grip: float, abort_on_collision=True):
        start = dict(self._pose)
        for i in range(1, n + 1):
            self._tick(blend(start, target, i / n), grip)
            if abort_on_collision and self._collisions:
                return False
        return True

    def _hold(self, n: int, grip: float, sample: bool = False):
        for _ in range(n):
            self._tick(dict(self._pose), grip)
            if sample:
                f, _pads = self._grasp_force()
                self._hold_forces.append(f)
                self._peak_force = max(self._peak_force, f)

    # ------------------------------------------------------------- sensing
    def _obstacle_contact(self) -> bool:
        pts = self._p.getContactPoints(bodyA=self.robot, bodyB=self.obstacle,
                                       physicsClientId=self._cid)
        return bool(pts)

    def _grasp_force(self):
        pts = self._p.getContactPoints(bodyA=self.robot, bodyB=self.cube,
                                       physicsClientId=self._cid)
        total, pads = 0.0, set()
        for pt in pts:
            link = pt[3]
            if link in self._pad_links:
                total += abs(float(pt[9]))     # normalForce
                pads.add(link)
        return total, len(pads)

    def _cube_pos(self):
        pos, _orn = self._p.getBasePositionAndOrientation(
            self.cube, physicsClientId=self._cid)
        return [float(v) for v in pos]

    def _tip_pos(self):
        st = self._p.getLinkState(self.robot, self._wrist_link,
                                  computeForwardKinematics=True,
                                  physicsClientId=self._cid)
        pos, orn = st[4], st[5]
        rot = self._p.getMatrixFromQuaternion(orn)
        off = (0.0, 0.0, -GRIP_MID)
        return [pos[i] + sum(rot[3 * i + k] * off[k] for k in range(3))
                for i in range(3)]

    def _attach(self):
        p, c = self._p, self._cid
        self._constraint = p.createConstraint(
            self.robot, self._wrist_link, self.cube, -1,
            p.JOINT_POINT2POINT, [0, 0, 0],
            parentFramePosition=[0, 0, -GRIP_MID],
            childFramePosition=[0, 0, 0], physicsClientId=c)
        p.changeConstraint(self._constraint, maxForce=200, physicsClientId=c)

    # ---------------------------------------------------------------- skill
    def pick_object(self, params: dict | None = None) -> PickResult:
        name, key, scene = resolve_scene(params)

        t0 = time.perf_counter()
        self._build(scene)
        self._budget = scene["budget"]
        start_pos = self._cube_pos()
        grasp_state, stage = "open", "home"

        def report(success, reason, note=""):
            hold = (sum(self._hold_forces) / len(self._hold_forces)
                    if self._hold_forces else 0.0)
            return PickResult(success, reason, build_metrics(
                engine=ENGINE, obj=name, scene_key=key, stage=stage,
                grasp_state=grasp_state, start_pos=start_pos,
                end_pos=self._cube_pos(), hold_force=hold,
                peak_force=self._peak_force,
                contact_samples=self._contact_samples,
                collisions=self._collisions, steps=self._steps,
                budget=self._budget, wall_time=time.perf_counter() - t0,
                note=note))

        target = (scene["cube"][0], scene["cube"][1], CUBE_HALF)
        planar = math.hypot(target[0], target[1])

        try:
            if planar > WORK_R + 0.02:
                stage = "stretch"
                self._run(KEYFRAMES["stretch"], STAGE_STEPS["move_above"],
                          FINGER_OPEN, abort_on_collision=False)
                tip = self._tip_pos()
                gap = math.dist(tip, target)
                if gap > UNREACHABLE_GAP:
                    return report(False, "unreachable",
                                  f"tip stopped {gap:.3f} m short of the object")

            stage = "move_above"
            if not self._run(KEYFRAMES["above"], STAGE_STEPS["move_above"], FINGER_OPEN):
                return report(False, "collision", "obstacle struck during approach")

            stage = "descend"
            if not self._run(KEYFRAMES["grasp"], STAGE_STEPS["descend"], FINGER_OPEN):
                return report(False, "collision", "obstacle struck during descent")

            stage = "grip"
            n = STAGE_STEPS["grip"]
            for i in range(1, n + 1):
                self._tick(dict(self._pose), aperture_at(i / n))
                if self._collisions:
                    return report(False, "collision", "obstacle struck while closing")
                f, _pads = self._grasp_force()
                if f > 0.0:
                    self._contact_samples += 1
                self._peak_force = max(self._peak_force, f)

            force, pads = self._grasp_force()
            self._peak_force = max(self._peak_force, force)
            if self._peak_force < GRASP_FORCE_MIN or pads < 2:
                grasp_state = "slipped"
                return report(False, "grasp_failed",
                              f"pads={pads} peak_force={self._peak_force:.3f} N")
            self._attach()
            grasp_state = "attached"

            stage = "lift"
            if not self._run(KEYFRAMES["lift"], STAGE_STEPS["lift"], FINGER_CLOSED):
                return report(False, "collision", "obstacle struck during lift")

            stage = "settle"
            self._hold(STAGE_STEPS["settle"], FINGER_CLOSED, sample=True)

        except BudgetExhausted:
            return report(False, "timeout",
                          f"step budget {self._budget} exhausted in stage {stage}")

        lifted = self._cube_pos()[2] - start_pos[2]
        if lifted < LIFT_MIN:
            grasp_state = "slipped"
            return report(False, "grasp_failed", f"object rose only {lifted:.3f} m")
        return report(True, "picked", f"object lifted {lifted:.3f} m")


__all__ = ["PyBulletSimulator", "available", "ENGINE"]
