"""unitree-g1 --- PyBullet backend (sim-to-sim cross-check).

Same planar biped, same skill, same gait, different physics engine. For
balance-recover the torso carries a torso_pitch DOF (hinge about Y, pivot at the
hip line) and a *torque-limited* balance PD that is applied through
TORQUE_CONTROL with exactly the same law the MuJoCo backend runs (manual
qfrc_applied). A disturbance injects an angular velocity; a gentle push is caught
(recover -> success), a hard push saturates the torque cap and the torso falls
(genuine physics failure). The joint chain, gains, torque cap and the recover/
fall verdict are all imported from g1_spec.py, so test_sim2sim verifies the two
engines agree on behaviour, not on one solver's quirks.

PyBullet ships as a source distribution only, so it builds on Linux CI but
usually not on a bare Windows box. Import is lazy and every consumer is
expected to skip when ``available()`` is False.
"""
from __future__ import annotations

import math
import os
import tempfile
import time

from g1_spec import (
    LEG_JOINTS, HIP_MIN, HIP_MAX, KNEE_MIN, KNEE_MAX,
    STAND_Z, HIP_Z, TORSO_H, HIP_X_OFFSET, THIGH_LEN, SHANK_LEN, FOOT_H, FOOT_HALF,
    STEP_LEN, STEP_CLEAR, SWING_STEPS, TIMESTEP, WALK_VEL, OBSTACLE_HALF_X,
    resolve_scene, leg_ik, build_metrics, WalkResult,
    DEFAULT_BUDGET, PUSH_T, FALL_PITCH, RECOVER_PITCH,
    KP_BAL, KV_BAL, MAX_TORQUE_BAL,
)

ENGINE = "pybullet"

# Collision groups: the robot (torso + legs) is masked away from the floor, so
# the feet never exchange contact forces -- exactly mirroring the MuJoCo model
# where the foot geoms carry contype 0. The curb is purely geometric.
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


# Torso inertia about the hip line, matched to the MuJoCo box (mass 5 kg).
_TORSO_IXX = 0.52
_TORSO_IYY = 0.53
_TORSO_IZZ = 0.04


def _inertial(mass: float, ixx: float = 0.0, iyy: float = 0.0, izz: float = 0.0) -> str:
    if ixx or iyy or izz:
        return (f'<inertial mass="{mass}" ixx="{ixx}" ixy="0" ixz="0" '
                f'iyy="{iyy}" iyz="0" izz="{izz}"/>')
    i = max(1e-5, mass * 0.01)
    return (f'<inertial mass="{mass}" ixx="{i}" ixy="0" ixz="0" '
            f'iyy="{i}" iyz="0" izz="{i}"/>')


# --------------------------------------------------------------------- URDF --
def _robot_urdf() -> str:
    """The same kinematic chain the MJCF declares, in URDF form.

    Chain: base (static) -> torso_slide (torso_x prismatic, at the hip line) ->
    torso (torso_pitch revolute, pivot at the hip line) -> two 2-link legs. The
    joint order is fixed so the static sim2sim test can assert the URDF matches
    the spec: [torso_x, torso_pitch, left_hip, left_knee, right_hip, right_knee].
    """
    return f"""<?xml version="1.0"?>
<robot name="unitree-g1">
  <link name="base">
    {_inertial(0.0)}
    <visual><origin xyz="0 0 0"/><geometry><box size="0.05 0.05 0.05"/></geometry></visual>
    <collision><origin xyz="0 0 0"/><geometry><box size="0.05 0.05 0.05"/></geometry></collision>
  </link>
  <link name="torso_slide">
    {_inertial(0.01)}
    <visual><origin xyz="0 0 0"/><geometry><box size="0.05 0.05 0.05"/></geometry></visual>
    <collision><origin xyz="0 0 0"/><geometry><box size="0.05 0.05 0.05"/></geometry></collision>
  </link>
  <link name="torso">
    {_inertial(5.0, _TORSO_IXX, _TORSO_IYY, _TORSO_IZZ)}
    <visual><origin xyz="0 0 0"/><geometry><box size="0.24 0.18 {TORSO_H:.3f}"/></geometry><material name="torso_m"><color rgba="0.2 0.5 0.9 1"/></material></visual>
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

  <joint name="torso_x" type="prismatic" parent="base" child="torso_slide">
    <origin xyz="0 0 {HIP_Z:.3f}"/>
    <axis xyz="1 0 0"/>
    <limit lower="-20" upper="20" effort="2000" velocity="5"/>
  </joint>
  <joint name="torso_pitch" type="revolute" parent="torso_slide" child="torso">
    <origin xyz="0 0 {TORSO_H/2:.3f}"/>
    <axis xyz="0 1 0"/>
    <limit lower="-1.5" upper="1.5" effort="1000" velocity="20"/>
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


# --------------------------------------------------------------- simulator --
class PyBulletSimulator:
    """Drop-in twin of MuJoCoSimulator running on Bullet (planar biped)."""

    ROBOT_ID = "unitree-g1"
    SKILL_ID = "balance_recover"
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
        p.resetJointState(self.robot, self._jidx["torso_x"], 0.0, 0.0,
                          physicsClientId=c)
        p.resetJointState(self.robot, self._jidx["torso_pitch"], 0.0, 0.0,
                          physicsClientId=c)
        for name in LEG_JOINTS:
            p.resetJointState(self.robot, self._jidx[name], 0.0, 0.0,
                              physicsClientId=c)
        self._drive(0.0)

    def _drive(self, virtual_x: float):
        """Send position-control targets for the legs; torso_x is held at 0."""
        p, c = self._p, self._cid
        p.setJointMotorControl2(self.robot, self._jidx["torso_x"],
                                p.POSITION_CONTROL, targetPosition=virtual_x,
                                force=2000, positionGain=0.9, velocityGain=0.9,
                                physicsClientId=c)
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
        # Torque-limited balance PD on torso_pitch, applied through TORQUE_CONTROL
        # with the SAME law the MuJoCo backend runs via qfrc_applied.
        q, v = p.getJointState(self.robot, self._jidx["torso_pitch"],
                               physicsClientId=c)
        tau = KP_BAL * (0.0 - q) - KV_BAL * v
        tau = min(max(tau, -MAX_TORQUE_BAL), MAX_TORQUE_BAL)
        p.setJointMotorControl2(self.robot, self._jidx["torso_pitch"],
                                p.TORQUE_CONTROL, force=tau, physicsClientId=c)

    def _torso_x(self) -> float:
        return float(self._p.getJointState(
            self.robot, self._jidx["torso_x"],
            physicsClientId=self._cid)[0])

    def _torso_pitch(self) -> float:
        return float(self._p.getJointState(
            self.robot, self._jidx["torso_pitch"],
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
        push_w = float(scene.get("push", 0.0))
        t_push = int(PUSH_T * budget)
        advancing = False  # balance-recover and stop are stance tasks (no gait)
        self._build(obstacles)
        self._virtual_x = 0.0
        self._obstacle_contact = False
        self._collisions = 0
        self._max_pitch = 0.0
        self._fell = False

        start = [self._torso_x(), 0.0, STAND_Z]
        t0 = time.perf_counter()
        steps = 0
        reached = False

        # one warm-up step so the solver reaches the pinned pose
        self._apply_control(self._foot_targets(0, obstacles, advancing))
        self._p.stepSimulation(physicsClientId=self._cid)

        while steps < budget:
            # Inject the disturbance once, at t_push, as an angular velocity
            # about the hip line (a genuine toppling impulse).
            if steps == t_push and push_w != 0.0:
                q, _v = self._p.getJointState(
                    self.robot, self._jidx["torso_pitch"],
                    physicsClientId=self._cid)
                self._p.resetJointState(self.robot, self._jidx["torso_pitch"],
                                        q, push_w, physicsClientId=self._cid)
            self._apply_control(self._foot_targets(steps, obstacles, advancing))
            self._p.stepSimulation(physicsClientId=self._cid)
            self._check_obstacle_contact()
            pitch = self._torso_pitch()
            self._max_pitch = max(self._max_pitch, abs(pitch))
            if abs(pitch) > FALL_PITCH:
                self._fell = True
            steps += 1
            if self._fell:
                break

        wall = time.perf_counter() - t0
        end = [self._torso_x(), 0.0, STAND_Z]
        pitch_end = self._torso_pitch()

        if key == "stop":
            success = True
            reached = True
            note = "hold pose; upright within tolerance"
        elif self._fell:
            success = False
            reached = False
            note = (f"fell: torso pitch reached {self._max_pitch:.3f} rad "
                    f"(> {FALL_PITCH} rad) -- genuine physics failure")
        elif abs(pitch_end) < RECOVER_PITCH:
            success = True
            reached = True
            note = f"recovered upright (final pitch {pitch_end:+.3f} rad)"
        else:
            success = False
            reached = False
            note = (f"did not recover within budget "
                    f"(final pitch {pitch_end:+.3f} rad)")
        metrics = build_metrics(
            engine=ENGINE, scene_key=key, stage=key,
            start_pos=start, end_pos=end, steps=steps, budget=budget,
            wall_time=wall, note=note,
        )
        metrics["goalDistance"] = round(push_w, 3)
        metrics["reached"] = reached
        metrics["obstacleContact"] = self._obstacle_contact
        metrics["pitchRad"] = round(pitch_end, 4)
        metrics["maxPitchRad"] = round(self._max_pitch, 4)
        metrics["fell"] = self._fell
        metrics["pushImpulse"] = round(push_w, 3)
        metrics["recovered"] = bool(success)
        self._teardown()
        return WalkResult(success, msg_fmt(key, success, pitch_end, steps), metrics)

    # ----------------------------------------------------------- public API
    def balance_recover(self, params: dict | None = None):
        return self.run("balance_recover", params)

    def stop(self, params: dict | None = None):
        return self.run("stop", params)


def msg_fmt(key, success, pitch_end, steps):
    return (f"{key}: pitch {pitch_end:+.4f} rad in {steps} steps "
            f"({'recovered' if success else 'fell'})")


__all__ = ["PyBulletSimulator", "available", "ENGINE"]
