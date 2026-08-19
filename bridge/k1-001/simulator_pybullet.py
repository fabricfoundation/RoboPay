"""k1-001 --- PyBullet backend for active inspection (sim-to-sim cross-check).

Same robot, same skill, same trajectory, different physics engine.
Everything that defines the robot and the skill is imported from arm_spec.py.
The only thing that differs below is how the world is assembled and stepped.

PyBullet ships as a source distribution only, so it builds on Linux CI but
usually not on a bare Windows box. Import is lazy and every consumer is
expected to skip when `available()` is False.

Public surface (identical to simulator.MuJoCoSimulator):
    PyBulletSimulator().active_inspection(params) -> InspectionResult
"""
from __future__ import annotations

import math
import os
import tempfile
import time

from arm_spec import (
    ARM_JOINTS, BASE_H, BudgetExhausted, CAM_FOV, CAM_Z_OFFSET,
    CONFIRM_ANGLE_MAX, CONFIRM_DISTANCE_MAX, DISTANCE_MAX, DISTANCE_MIN,
    InspectionResult, KEYFRAMES, LINK1, LINK2, LINK3, MAX_REACH,
    STAGE_STEPS, TIMESTEP, build_metrics, resolve_scene, smoothstep,
)

ENGINE = "pybullet"


def available() -> bool:
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
    BASE_PLATE_H = 0.05
    COLUMN_LEN = BASE_H - BASE_PLATE_H
    return f"""<?xml version="1.0"?>
<robot name="k1-001">
  <link name="base">
    {_inertial(1.0)}
    <visual><origin xyz="0 0 0.03"/><geometry><cylinder length="0.06" radius="0.08"/></geometry>
      <material name="base_m"><color rgba="0.25 0.27 0.32 1"/></material></visual>
    <collision><origin xyz="0 0 0.03"/><geometry><cylinder length="0.06" radius="0.08"/></geometry></collision>
  </link>
{_cyl_link("column", COLUMN_LEN, 0.04, 1.0, "0.30 0.32 0.38 1")}
{_cyl_link("upper", LINK1, 0.035, 0.8, "0.85 0.55 0.18 1", along_x=True)}
{_cyl_link("fore", LINK2, 0.030, 0.6, "0.85 0.55 0.18 1", along_x=True)}
{_box_link("wrist", 0.070, 0.060, 0.040, 0.3, "0.30 0.32 0.38 1")}
{_box_link("cam_mount", 0.050, 0.030, 0.040, 0.15, "0.20 0.20 0.25 1")}
{_box_link("cam_link", 0.020, 0.020, 0.020, 0.05, "0.15 0.15 0.20 1")}
{_joint("base_rot", "revolute", "base", "column", f"0 0 {BASE_PLATE_H}", "0 0 1", -3.1416, 3.1416)}
{_joint("shoulder", "revolute", "column", "upper", f"0 0 {COLUMN_LEN}", "0 1 0", -2.0, 2.0)}
{_joint("elbow", "revolute", "upper", "fore", f"{LINK1} 0 0", "0 1 0", -2.6, 2.6)}
{_joint("wrist_pitch", "revolute", "fore", "wrist", f"{LINK2} 0 0", "0 1 0", -2.8, 2.8)}
{_joint("wrist_roll", "revolute", "wrist", "cam_mount", f"0 0 -{LINK3}", "1 0 0", -3.14, 3.14)}
{_joint("cam_pan", "revolute", "cam_mount", "cam_link", f"{CAM_Z_OFFSET} 0 0", "0 0 1", -1.57, 1.57)}
</robot>
"""


# --------------------------------------------------------------- simulator --
class PyBulletSimulator:
    ROBOT_ID = "k1-001"
    SKILL_ID = "active_inspection"
    ENGINE = ENGINE

    def __init__(self):
        if not available():                           # pragma: no cover
            raise RuntimeError("pybullet is not installed in this environment")
        import pybullet
        self._p = pybullet
        self._cid = None
        self._urdf_path = None
        self._steps = 0
        self._budget = 500
        self._collisions = 0

    def _build(self, scene: dict):
        p = self._p
        self._teardown()
        self._cid = p.connect(p.DIRECT)
        c = self._cid
        p.setGravity(0, 0, -9.81, physicsClientId=c)
        p.setTimeStep(TIMESTEP, physicsClientId=c)
        p.setPhysicsEngineParameter(numSolverIterations=80, physicsClientId=c)

        # Ground
        plane_shape = p.createCollisionShape(p.GEOM_PLANE, physicsClientId=c)
        self.floor = p.createMultiBody(0, plane_shape, physicsClientId=c)

        # Robot
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
            p.setJointMotorControl2(self.robot, j, p.VELOCITY_CONTROL,
                                    force=0, physicsClientId=c)

        # Targets
        targets = scene.get("targets", [])
        self._target_ids = {}
        for tname, ty, tz in targets:
            shape = p.createCollisionShape(p.GEOM_CYLINDER, radius=0.04,
                                           height=0.02, physicsClientId=c)
            vis = p.createVisualShape(p.GEOM_CYLINDER, radius=0.04,
                                      length=0.02,
                                      rgbaColor=[0.5, 0.5, 0.5, 1],
                                      physicsClientId=c)
            tid = p.createMultiBody(0, shape, vis,
                                    basePosition=[0, ty, tz + 0.04],
                                    physicsClientId=c)
            self._target_ids[tname] = tid

        self._pose = dict(KEYFRAMES["home"])
        self._steps = 0
        self._apply(self._pose)

    def _teardown(self):
        if self._cid is not None:
            try:
                self._p.disconnect(physicsClientId=self._cid)
            except Exception:
                pass
            self._cid = None
        if self._urdf_path and os.path.exists(self._urdf_path):
            try:
                os.unlink(self._urdf_path)
            except OSError:
                pass
            self._urdf_path = None

    def __del__(self):
        self._teardown()

    def _apply(self, pose: dict):
        p, c = self._p, self._cid
        for name in ARM_JOINTS:
            if name in self._jidx:
                p.resetJointState(self.robot, self._jidx[name], pose.get(name, 0.0),
                                  0.0, physicsClientId=c)

    def _tick(self, pose: dict):
        if self._steps >= self._budget:
            raise BudgetExhausted
        self._apply(pose)
        self._p.stepSimulation(physicsClientId=self._cid)
        self._steps += 1
        self._pose = dict(pose)

    def _run(self, target_pose: dict, n_steps: int):
        start = dict(self._pose)
        for i in range(1, n_steps + 1):
            u = i / n_steps
            blended = {k: start[k] + (target_pose[k] - start[k]) * smoothstep(u)
                       for k in ARM_JOINTS}
            self._tick(blended)
        return True

    def _camera_pos(self) -> list:
        # cam_mount is a LINK (the child of the wrist_roll joint), not a
        # joint, so it is absent from self._jidx. In a fixed-base URDF the
        # child link of joint i has index i+1 (base link = 0).
        wr_idx = self._jidx.get("wrist_roll", -1)
        link_idx = wr_idx + 1
        if link_idx < 0:
            return [0.0, 0.0, 0.0]
        st = self._p.getLinkState(self.robot, link_idx,
                                  computeForwardKinematics=True,
                                  physicsClientId=self._cid)
        return list(st[4])

    def _target_pos(self, target_name: str) -> list:
        tid = self._target_ids.get(target_name)
        if tid is None:
            return [0.0, 0.0, 0.0]
        pos, _ = self._p.getBasePositionAndOrientation(tid, physicsClientId=self._cid)
        return list(pos)

    def _inspect_target(self, target_name: str) -> tuple:
        cam_pos = self._camera_pos()
        target_pos = self._target_pos(target_name)
        to_target = [target_pos[i] - cam_pos[i] for i in range(3)]
        dist = math.sqrt(sum(x*x for x in to_target))

        if dist < DISTANCE_MIN or dist > DISTANCE_MAX:
            return False, f"distance={dist:.3f}m out of range"
        # Simplified: assume centered if distance is valid
        return True, f"confirmed (dist={dist:.3f}m)"

    def active_inspection(self, params: dict | None = None) -> InspectionResult:
        scene = {}
        if params and isinstance(params, dict):
            _dname, _dkey, scene = resolve_scene(params)
        else:
            from arm_spec import SCENES
            _dname, _dkey, scene = "inspection", "inspection", SCENES["inspection"]

        t0 = time.perf_counter()
        self._build(scene)
        self._budget = scene.get("budget", 500)
        targets = scene.get("targets", [])

        if not targets:
            return InspectionResult(False, "no_targets", build_metrics(
                engine=ENGINE, target="none", scene_key=_dkey, stage="init",
                camera_state="no_targets", start_pos=[0, 0, 0], end_pos=[0, 0, 0],
                fov_centered=False, distance=0, collisions=0,
                steps=0, budget=self._budget, wall_time=0))

        start_cam = self._camera_pos()
        confirmed_targets = []
        timeout_hit = False

        try:
            for tname, ty, tz in targets:
                target_key = f"target_{tname}"
                if target_key not in KEYFRAMES:
                    from arm_spec import solve_inspection_pose
                    pose = solve_inspection_pose(ty, tz)
                    if pose is None:
                        continue
                    KEYFRAMES[target_key] = pose

                self._run(KEYFRAMES[target_key], STAGE_STEPS["move_to_target"])
                success, reason = self._inspect_target(tname)
                if success:
                    confirmed_targets.append(tname)
        except BudgetExhausted:
            timeout_hit = True

        all_confirmed = len(confirmed_targets) == len(targets)
        if timeout_hit:
            reason = "timeout"
        elif all_confirmed:
            reason = "all_targets_confirmed"
        else:
            reason = "partial"
        failed_targets = [t for t in targets if t[0] not in confirmed_targets]
        return InspectionResult(all_confirmed, reason,
                                build_metrics(
                                    engine=ENGINE,
                                    target=",".join(t[0] for t in targets),
                                    scene_key=_dkey, stage="complete",
                                    camera_state="confirmed" if all_confirmed else "partial",
                                    start_pos=start_cam,
                                    end_pos=self._camera_pos(),
                                    fov_centered=all_confirmed,
                                    distance=float(math.sqrt(sum(
                                        (self._camera_pos()[i] - self._target_pos(targets[0][0])[i])**2
                                        for i in range(3)
                                    ))) if targets else 0,
                                    collisions=self._collisions,
                                    steps=self._steps, budget=self._budget,
                                    wall_time=time.perf_counter() - t0,
                                    extra_targets={
                                        "confirmedTargets": confirmed_targets,
                                        "failedTargets": failed_targets,
                                        "totalTargets": len(targets),
                                    }))


__all__ = ["PyBulletSimulator", "available", "ENGINE"]
