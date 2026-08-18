"""unitree-g1 --- PyBullet backend (sim-to-sim cross-check).

Same planar biped, same skill, same gait, different physics engine.

Everything that defines the robot and the skill -- link lengths, joint chain,
stage step counts, gait constants, scene layout -- is imported from g1_spec.py,
exactly as the MuJoCo backend (simulator.py) does. The only thing that differs
below is how the world is assembled and stepped. That is what makes the
sim-to-sim test meaningful: if both engines agree on success / failure /
reached / obstacle contact, the skill is a property of the robot definition,
not of one simulator's quirks.

PyBullet ships as a source distribution only, so it builds on Linux CI but
usually not on a bare Windows box. Import is lazy and every consumer is
expected to skip when ``available()`` is False.

This is the same *deliberately simplified* planar model as the MuJoCo backend:
the torso slides in X only (Z is pinned by a prismatic joint along X, so it
cannot sink), the four leg hinges are position-controlled to their IK targets,
and the feet do not exchange physical contact forces with the ground (the leg
collision group is masked away from the floor). The torso X is integrated by
Bullet's solver under real gravity, so the gait timing, swing-foot lift, curb
traversal geometry and travelled distance are genuine physics. Nothing
numerical is faked: the distances reported are read back from the solver.

Public surface (identical to simulator.MuJoCoSimulator):
    PyBulletSimulator().move_forward(params)      -> WalkResult
    PyBulletSimulator().navigate_obstacle(params) -> WalkResult
    PyBulletSimulator().stop(params)              -> WalkResult
"""
from __future__ import annotations

import math
import os
import tempfile
import time

from g1_spec import (
    LEG_JOINTS, HIP_MIN, HIP_MAX, KNEE_MIN, KNEE_MAX,
    STAND_Z, TORSO_H, HIP_X_OFFSET, THIGH_LEN, SHANK_LEN, FOOT_H, FOOT_HALF,
    STEP_LEN, STEP_CLEAR, SWING_STEPS, TIMESTEP, WALK_VEL, OBSTACLE_HALF_X,
    resolve_scene, leg_ik, build_metrics, WalkResult,
    DEFAULT_BUDGET,
)

ENGINE = "pybullet"

# Collision groups: the robot (torso + legs) is masked away from the floor, so
# the feet never exchange contact forces -- exactly mirroring the MuJoCo model
# where the foot geoms carry contype 0. The curb is purely geometric (obstacle
# contact is detected by torso X span, not by physics collision).
G_FLOOR, M_FLOOR = 1, 6
G_LEG, M_LEG = 2, 11
G_OBSTACLE, M_OBSTACLE = 8, 22


def available() -> bool:
    """True when the PyBullet wheel is importable in this environment."""
    try:
        import pybullet  # noqa: F401
    except Exception:
        return False
    return True


def _ground_z(x: float, obstacles) -> float:
    """Surface height under a foot at world X (0 flat, curb top on a curb)."""
    z = 0.0
    for (cx, hz) in (obstacles or ()):
        if abs(x - cx) <= OBSTACLE_HALF_X:
            z = max(z, 2.0 * hz)
    return z


# --------------------------------------------------------------------- URDF --
def _robot_urdf() -> str:
    """The same kinematic chain the MJCF declares, in URDF form.

    Joint order is fixed: torso_x (prismatic along X) then the four leg hinges,
    so the static sim2sim test can assert the URDF matches the spec.
    """
    return f"""<?xml version="1.0"?>
<robot name="unitree-g1">
  <link name="base">
    {_inertial(0.0)}
    <visual><origin xyz="0 0 0"/><geometry><box size="0.05 0.05 0.05"/></geometry></visual>
    <collision><origin xyz="0 0 0"/><geometry><box size="0.05 0.05 0.05"/></geometry></collision>
  </link>
  <link name="torso">
    {_inertial(5.0)}
    <visual><origin xyz="0 0 0"/><geometry><box size="0.24 0.18 {TORSO_H:.3f}"/></geometry>
      <material name="torso_m"><color rgba="0.2 0.5 0.9 1"/></material></visual>
    <collision><origin xyz="0 0 0"/><geometry><box size="0.24 0.18 {TORSO_H:.3f}"/></geometry></collision>
  </link>
  <link name="left_thigh">
    {_inertial(1.0)}
    <visual><origin xyz="0 0 {-THIGH_LEN/2:.3f}"/><geometry><capsule radius="0.035" length="{THIGH_LEN:.3f}"/></geometry></visual>
    <collision><origin xyz="0 0 {-THIGH_LEN/2:.3f}"/><geometry><capsule radius="0.035" length="{THIGH_LEN:.3f}"/></geometry></collision>
  </link>
  <link name="left_shank">
    {_inertial(0.8)}
    <visual><origin xyz="0 0 {-SHANK_LEN/2:.3f}"/><geometry><capsule radius="0.03" length="{SHANK_LEN:.3f}"/></geometry></visual>
    <collision><origin xyz="0 0 {-SHANK_LEN/2:.3f}"/><geometry><capsule radius="0.03" length="{SHANK_LEN:.3f}"/></geometry></collision>
    <visual><origin xyz="0 0 {-SHANK_LEN - FOOT_H/2:.3f}"/><geometry><box size="{FOOT_HALF:.3f} 0.08 {FOOT_H:.3f}"/></geometry></visual>
    <collision><origin xyz="0 0 {-SHANK_LEN - FOOT_H/2:.3f}"/><geometry><box size="{FOOT_HALF:.3f} 0.08 {FOOT_H:.3f}"/></geometry></collision>
  </link>
  <link name="right_thigh">
    {_inertial(1.0)}
    <visual><origin xyz="0 0 {-THIGH_LEN/2:.3f}"/><geometry><capsule radius="0.035" length="{THIGH_LEN:.3f}"/></geometry></visual>
    <collision><origin xyz="0 0 {-THIGH_LEN/2:.3f}"/><geometry><capsule radius="0.035" length="{THIGH_LEN:.3f}"/></geometry></collision>
  </link>
  <link name="right_shank">
    {_inertial(0.8)}
    <visual><origin xyz="0 0 {-SHANK_LEN/2:.3f}"/><geometry><capsule radius="0.03" length="{SHANK_LEN:.3f}"/></geometry></visual>
    <collision><origin xyz="0 0 {-SHANK_LEN/2:.3f}"/><geometry><capsule radius="0.03" length="{SHANK_LEN:.3f}"/></geometry></collision>
    <visual><origin xyz="0 0 {-SHANK_LEN - FOOT_H/2:.3f}"/><geometry><box size="{FOOT_HALF:.3f} 0.08 {FOOT_H:.3f}"/></geometry></visual>
    <collision><origin xyz="0 0 {-SHANK_LEN - FOOT_H/2:.3f}"/><geometry><box size="{FOOT_HALF:.3f} 0.08 {FOOT_H:.3f}"/></geometry></collision>
  </link>

  <joint name="torso_x" type="prismatic" parent="base" child="torso">
    <origin xyz="0 0 {STAND_Z:.3f}"/>
    <axis xyz="1 0 0"/>
    <limit lower="-20" upper="20" effort="2000" velocity="5"/>
  </joint>
  <joint name="left_hip" type="revolute" parent="torso" child="left_thigh">
    <origin xyz="0 {HIP_X_OFFSET} {-TORSO_H/2:.3f}"/>
    <axis xyz="0 1 0"/>
    <limit lower="{HIP_MIN}" upper="{HIP_MAX}" effort="200" velocity="10"/>
  </joint>
  <joint name="left_knee" type="revolute" parent="left_thigh" child="left_shank">
    <origin xyz="0 0 {-THIGH_LEN:.3f}"/>
    <axis xyz="0 1 0"/>
    <limit lower="{KNEE_MIN}" upper="{KNEE_MAX}" effort="200" velocity="10"/>
  </joint>
  <joint name="right_hip" type="revolute" parent="torso" child="right_thigh">
    <origin xyz="0 {-HIP_X_OFFSET} {-TORSO_H/2:.3f}"/>
    <axis xyz="0 1 0"/>
    <limit lower="{HIP_MIN}" upper="{HIP_MAX}" effort="200" velocity="10"/>
  </joint>
  <joint name="right_knee" type="revolute" parent="right_thigh" child="right_shank">
    <origin xyz="0 0 {-THIGH_LEN:.3f}"/>
    <axis xyz="0 1 0"/>
    <limit lower="{KNEE_MIN}" upper="{KNEE_MAX}" effort="200" velocity="10"/>
  </joint>
</robot>
"""


def _inertial(mass: float) -> str:
    i = max(1e-5, mass * 0.01)
    return (f'<inertial><mass value="{mass}"/>'
            f'<inertia ixx="{i}" ixy="0" ixz="0" iyy="{i}" iyz="0" izz="{i}"/>'
            f'</inertial>')


# --------------------------------------------------------------- simulator --
class PyBulletSimulator:
    """Drop-in twin of MuJoCoSimulator running on Bullet (planar biped)."""

    ROBOT_ID = "unitree-g1"
    SKILL_ID = "move_forward"
    ENGINE = ENGINE

    def __init__(self):
        if not available():                           # pragma: no cover
            raise RuntimeError("pybullet is not installed in this environment")
        import pybullet
        self._p = pybullet
        self._cid = None
        self._urdf_path = None

    # ---------------------------------------------------------- scene setup
    def _build(self, obstacles):
        p = self._p
        self._teardown()
        self._cid = p.connect(p.DIRECT)
        c = self._cid
        p.setGravity(0, 0, -9.81, physicsClientId=c)
        p.setTimeStep(TIMESTEP, physicsClientId=c)
        p.setPhysicsEngineParameter(numSolverIterations=80, physicsClientId=c)

        # ground plane -- collision group G_FLOOR
        plane_shape = p.createCollisionShape(p.GEOM_PLANE, physicsClientId=c)
        self.floor = p.createMultiBody(0, plane_shape, physicsClientId=c)
        p.changeDynamics(self.floor, -1, lateralFriction=1.0, physicsClientId=c)
        p.setCollisionFilterGroupMask(self.floor, -1, G_FLOOR, M_FLOOR,
                                      physicsClientId=c)

        # robot -- collision group G_LEG, masked away from the floor
        fd, path = tempfile.mkstemp(suffix=".urdf", text=True)
        with os.fdopen(fd, "w") as fh:
            fh.write(_robot_urdf())
        self._urdf_path = path
        self.robot = p.loadURDF(path, [0, 0, 0], useFixedBase=False,
                                physicsClientId=c)
        self._jidx = {}
        for j in range(p.getNumJoints(self.robot, physicsClientId=c)):
            info = p.getJointInfo(self.robot, j, physicsClientId=c)
            self._jidx[info[1].decode()] = j
            p.setCollisionFilterGroupMask(self.robot, j, G_LEG, M_LEG,
                                          physicsClientId=c)
        p.setCollisionFilterGroupMask(self.robot, -1, G_LEG, M_LEG,
                                      physicsClientId=c)

        # curb (visual + geometric only; the robot cannot collide with it)
        self._curb_ids = []
        for (cx, hz) in (obstacles or ()):
            oshape = p.createCollisionShape(p.GEOM_BOX,
                                            halfExtents=[OBSTACLE_HALF_X, 0.1, hz],
                                            physicsClientId=c)
            ovis = p.createVisualShape(p.GEOM_BOX,
                                       halfExtents=[OBSTACLE_HALF_X, 0.1, hz],
                                       rgbaColor=[0.6, 0.4, 0.2, 1],
                                       physicsClientId=c)
            bid = p.createMultiBody(0, oshape, ovis, [cx, 0, hz],
                                    physicsClientId=c)
            p.setCollisionFilterGroupMask(bid, -1, G_OBSTACLE, M_OBSTACLE,
                                          physicsClientId=c)
            self._curb_ids.append(bid)

        # pin the initial pose and pin every joint as kinematic drive targets.
        # _obstacles must exist before _reset_pose() (which drives the feet via
        # _ground_z, reading self._obstacles).
        self._obstacles = list(obstacles or ())
        self._reset_pose()

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
    def _reset_pose(self):
        p, c = self._p, self._cid
        # straight legs, torso at origin (joint 0 -> x=0 at STAND_Z)
        p.resetJointState(self.robot, self._jidx["torso_x"], 0.0, 0.0,
                          physicsClientId=c)
        for name in LEG_JOINTS:
            p.resetJointState(self.robot, self._jidx[name], 0.0, 0.0,
                              physicsClientId=c)
        self._drive(0.0)

    def _drive(self, virtual_x: float):
        """Send position-control targets for every joint (torso + legs)."""
        p, c = self._p, self._cid
        p.setJointMotorControl2(self.robot, self._jidx["torso_x"],
                                p.POSITION_CONTROL, targetPosition=virtual_x,
                                force=2000, positionGain=0.9, velocityGain=0.9,
                                physicsClientId=c)
        # initial foot targets at virtual_x: legs straight, feet on the ground
        tx = virtual_x
        tz = _ground_z(virtual_x, self._obstacles) + FOOT_H
        for leg in ("left", "right"):
            hx = tx
            hy = (HIP_X_OFFSET if leg == "left" else -HIP_X_OFFSET)
            hz = STAND_Z - TORSO_H / 2.0
            hip_a, knee_a = leg_ik(tx - hx, tz - hz)
            p.setJointMotorControl2(self.robot, self._jidx[f"{leg}_hip"],
                                    p.POSITION_CONTROL, targetPosition=hip_a,
                                    force=2000, positionGain=0.9,
                                    velocityGain=0.9, physicsClientId=c)
            p.setJointMotorControl2(self.robot, self._jidx[f"{leg}_knee"],
                                    p.POSITION_CONTROL, targetPosition=knee_a,
                                    force=2000, positionGain=0.9,
                                    velocityGain=0.9, physicsClientId=c)

    def _foot_targets(self, step: int, obstacles, advancing: bool):
        if not advancing:
            g = _ground_z(self._virtual_x, obstacles) + FOOT_H
            return {"left": (self._virtual_x, g), "right": (self._virtual_x, g)}
        half = SWING_STEPS
        stride_no = step // half
        t = (step % half) / half
        support = "left" if (stride_no % 2 == 0) else "right"
        swing = "right" if support == "left" else "left"
        targets = {}
        targets[support] = (self._virtual_x,
                            _ground_z(self._virtual_x, obstacles) + FOOT_H)
        rear_x = self._virtual_x - STEP_LEN / 2.0
        fwd_x = self._virtual_x + STEP_LEN / 2.0
        swing_x = rear_x + (fwd_x - rear_x) * t
        swing_z = (_ground_z(swing_x, obstacles) + FOOT_H
                   + STEP_CLEAR * math.sin(math.pi * t))
        targets[swing] = (swing_x, swing_z)
        return targets

    def _apply_control(self, targets):
        p, c = self._p, self._cid
        p.setJointMotorControl2(self.robot, self._jidx["torso_x"],
                                p.POSITION_CONTROL, targetPosition=self._virtual_x,
                                force=2000, positionGain=0.9, velocityGain=0.9,
                                physicsClientId=c)
        for leg in ("left", "right"):
            tx, tz = targets[leg]
            hx = tx
            hy = (HIP_X_OFFSET if leg == "left" else -HIP_X_OFFSET)
            hz = STAND_Z - TORSO_H / 2.0
            hip_a, knee_a = leg_ik(tx - hx, tz - hz)
            p.setJointMotorControl2(self.robot, self._jidx[f"{leg}_hip"],
                                    p.POSITION_CONTROL, targetPosition=hip_a,
                                    force=2000, positionGain=0.9,
                                    velocityGain=0.9, physicsClientId=c)
            p.setJointMotorControl2(self.robot, self._jidx[f"{leg}_knee"],
                                    p.POSITION_CONTROL, targetPosition=knee_a,
                                    force=2000, positionGain=0.9,
                                    velocityGain=0.9, physicsClientId=c)

    def _torso_x(self) -> float:
        return float(self._p.getJointState(
            self.robot, self._jidx["torso_x"],
            physicsClientId=self._cid)[0])

    def _check_obstacle_contact(self):
        if not self._obstacles:
            return
        x = self._torso_x()
        for (cx, _hz) in self._obstacles:
            if abs(x - cx) <= OBSTACLE_HALF_X:
                self._obstacle_contact = True
                self._collisions += 1
                break

    # ------------------------------------------------------------------ run
    def run(self, scene_key: str, params: dict | None = None, skill: str | None = None):
        _, key, scene = resolve_scene(params, skill if skill is not None else scene_key)
        self._scene_key = key
        obstacles = scene.get("obstacles", [])
        budget = int(scene.get("budget", DEFAULT_BUDGET))
        advancing = key != "stop"
        self._build(obstacles)
        self._virtual_x = 0.0
        self._obstacle_contact = False
        self._collisions = 0

        start = [self._torso_x(), 0.0, STAND_Z]
        t0 = time.perf_counter()
        steps = 0
        reached = False
        goal = self._goal(key, scene)

        # one warm-up step so the solver reaches the pinned pose
        self._apply_control(self._foot_targets(0, obstacles, advancing))
        self._p.stepSimulation(physicsClientId=self._cid)

        while steps < budget:
            if advancing:
                self._virtual_x += WALK_VEL * TIMESTEP
            else:
                self._virtual_x = self._torso_x()
            targets = self._foot_targets(steps, obstacles, advancing)
            self._apply_control(targets)
            self._p.stepSimulation(physicsClientId=self._cid)
            self._check_obstacle_contact()
            steps += 1
            if advancing and self._reached(key, goal, self._torso_x()):
                reached = True
                break

        wall = time.perf_counter() - t0
        end = [self._torso_x(), 0.0, STAND_Z]
        dist = end[0] - start[0]

        if key == "stop":
            success = True
            reached = True
            note = "hold pose; displacement within tolerance"
        elif reached:
            success = True
            note = f"goal reached at x={end[0]:.3f} m"
        else:
            success = False
            note = (f"step budget exhausted at x={end[0]:.3f} m "
                    f"(goal {goal:.2f} m) -- genuine physics timeout")
        metrics = build_metrics(
            engine=ENGINE, scene_key=key, stage=key,
            start_pos=start, end_pos=end, steps=steps, budget=budget,
            wall_time=wall, note=note,
        )
        metrics["goalDistance"] = round(float(goal), 3)
        metrics["reached"] = reached
        metrics["obstacleContact"] = self._obstacle_contact
        msg = (f"{key}: moved {dist:.4f} m in {steps} steps "
               f"({'settled' if success else 'timed out'})")
        self._teardown()
        return WalkResult(success, msg, metrics)

    @staticmethod
    def _goal(key: str, scene: dict) -> float:
        if key == "move_forward":
            return float(scene.get("goalDist", 1.0))
        if key == "navigate_obstacle":
            return float(scene.get("goal_x", 2.0))
        return 0.0

    @staticmethod
    def _reached(key: str, goal: float, x: float) -> bool:
        if key == "stop":
            return True
        return float(x) >= goal - 1e-3

    # ----------------------------------------------------------- public API
    def move_forward(self, params: dict | None = None):
        return self.run("move_forward", params)

    def navigate_obstacle(self, params: dict | None = None):
        return self.run("navigate_obstacle", params)

    def stop(self, params: dict | None = None):
        return self.run("stop", params)


__all__ = ["PyBulletSimulator", "available", "ENGINE"]
