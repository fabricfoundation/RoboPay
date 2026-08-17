"""door-arm-001 --- MuJoCo backend for the door-opening skill.

The skill: grip the door handle and pull the door open.
Success = door rotated past OPEN_ANGLE_MIN.
Failure modes: stuck (high friction), out_of_range (cannot reach handle).
"""
from __future__ import annotations

import time

import mujoco
import numpy as np

from arm_spec import (
    ARM_JOINTS, BASE_H, BudgetExhausted, DOOR_HANDLE_HEIGHT, DOOR_WIDTH,
    GRASP_FORCE_MIN, GRIP_MID, LINK1, LINK2, OPEN_ANGLE_MIN, TIMESTEP,
    aperture_at, blend, build_metrics, resolve_scene, DoorResult,
)

ENGINE = "mujoco"


def _model_xml(scene: dict) -> str:
    """MJCF for door-opening cell."""
    dx, dy = scene["door_x"], scene["door_y"]
    friction = scene.get("friction", 0.3)
    hz = scene.get("handle_z", DOOR_HANDLE_HEIGHT)

    return f"""
<mujoco model="door-arm-001">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="{TIMESTEP}" gravity="0 0 -9.81" integrator="implicitfast"/>
  <default>
    <joint damping="2" armature="0.01"/>
    <geom solref="0.006 1" solimp="0.95 0.99 0.001"/>
  </default>

  <worldbody>
    <light pos="0.4 0 1.6" dir="0 0 -1" diffuse="0.9 0.9 0.9"/>
    <geom name="floor" type="plane" size="2 2 0.05" rgba="0.16 0.18 0.22 1"
          contype="1" conaffinity="6" friction="1.0 0.01 0.001"/>

    <!-- Door frame -->
    <body name="frame_left" pos="-0.05 0 0">
      <geom name="frame_l_g" type="box" size="0.05 0.05 1.05"
            pos="0 0 1.05" rgba="0.4 0.4 0.45 1" contype="16" conaffinity="8"/>
    </body>
    <body name="frame_right" pos="{dx + DOOR_WIDTH + 0.05} 0 0">
      <geom name="frame_r_g" type="box" size="0.05 0.05 1.05"
            pos="0 0 1.05" rgba="0.4 0.4 0.45 1" contype="16" conaffinity="8"/>
    </body>
    <body name="frame_top" pos="{dx + DOOR_WIDTH/2} 0 {hz + 0.15}">
      <geom name="frame_t_g" type="box" size="{DOOR_WIDTH/2 + 0.05} 0.05 0.05"
            rgba="0.4 0.4 0.45 1" contype="16" conaffinity="8"/>
    </body>

    <!-- Door (hinged at left edge) -->
    <body name="door" pos="{dx} 0 0">
      <joint name="door_hinge" type="hinge" axis="0 0 1" range="0 1.57"
             damping="{friction}"/>
      <geom name="door_g" type="box" size="{DOOR_WIDTH/2} 0.03 {hz + 0.05}"
            pos="{DOOR_WIDTH/2} 0 {hz + 0.05}" rgba="0.85 0.65 0.35 1"
            contype="2" conaffinity="13" friction="{friction} 0.05 0.001"/>
      <!-- Handle -->
      <body name="handle" pos="{DOOR_WIDTH - 0.05} 0.04 {hz}">
        <joint name="handle_rot" type="hinge" axis="0 1 0" range="-0.5 0.5"/>
        <geom name="handle_g" type="cylinder" size="0.015 0.02"
              pos="0 0 0" rgba="0.6 0.6 0.65 1" contype="4" conaffinity="13"
              friction="0.5 0.05 0.001"/>
      </body>
    </body>

    <!-- Arm base -->
    <body name="base" pos="0 0 0">
      <geom name="base_g" type="cylinder" size="0.07 0.025" pos="0 0 0.025"
            rgba="0.25 0.27 0.32 1" contype="16" conaffinity="8"/>
      <body name="column" pos="0 0 {BASE_H - 0.35}">
        <joint name="pan" type="hinge" axis="0 0 1" range="-3.1416 3.1416"/>
        <geom name="column_g" type="capsule" fromto="0 0 0 0 0 0.35" size="0.035"
              rgba="0.30 0.32 0.38 1" contype="16" conaffinity="8"/>
        <body name="upper" pos="0 0 0.35">
          <joint name="shoulder" type="hinge" axis="0 1 0" range="-2.0 2.0"/>
          <geom name="upper_g" type="capsule" fromto="0 0 0 {LINK1} 0 0" size="0.030"
                rgba="0.85 0.55 0.18 1" contype="16" conaffinity="8"/>
          <body name="fore" pos="{LINK1} 0 0">
            <joint name="elbow" type="hinge" axis="0 1 0" range="-2.6 2.6"/>
            <geom name="fore_g" type="capsule" fromto="0 0 0 {LINK2} 0 0" size="0.026"
                  rgba="0.85 0.55 0.18 1" contype="16" conaffinity="8"/>
            <body name="wrist" pos="{LINK2} 0 0">
              <joint name="wristp" type="hinge" axis="0 1 0" range="-2.8 2.8"/>
              <geom name="wrist_g" type="box" size="0.032 0.030 0.018"
                    rgba="0.30 0.32 0.38 1" contype="16" conaffinity="8"/>
              <site name="grip_site" pos="0 0 -{GRIP_MID}" size="0.006"
                    rgba="0.9 0.9 0.2 0.4"/>
              <body name="finger_l" pos="0 0 -{GRIP_MID}">
                <joint name="grip_l" type="slide" axis="0 1 0" range="0.012 0.060"/>
                <geom name="finger_l_g" type="box"
                      size="0.014 0.008 0.045"
                      rgba="0.90 0.90 0.92 1" contype="4" conaffinity="11"
                      friction="0.5 0.05 0.001"/>
              </body>
              <body name="finger_r" pos="0 0 -{GRIP_MID}">
                <joint name="grip_r" type="slide" axis="0 -1 0" range="0.012 0.060"/>
                <geom name="finger_r_g" type="box"
                      size="0.014 0.008 0.045"
                      rgba="0.90 0.90 0.92 1" contype="4" conaffinity="11"
                      friction="0.5 0.05 0.001"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>

  <equality>
    <joint joint1="grip_l" joint2="grip_r" polycoef="0 -1 0 0 0"/>
  </equality>
</mujoco>
"""


class MuJoCoSimulator:
    ROBOT_ID = "door-arm-001"
    SKILL_ID = "open_door"
    ENGINE = ENGINE

    def __init__(self):
        self.model = None
        self.data = None
        self._steps = 0
        self._budget = 400
        self._door_angle = 0.0
        self._handle_angle = 0.0

    def _build(self, scene: dict):
        xml = _model_xml(scene)
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)

        m = self.model
        self._qadr, self._vadr = {}, {}
        for name in ARM_JOINTS:
            jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
            self._qadr[name] = m.jnt_qposadr[jid]
            self._vadr[name] = m.jnt_dofadr[jid]
        for name in ("grip_l", "grip_r"):
            jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
            self._qadr[name] = m.jnt_qposadr[jid]
            self._vadr[name] = m.jnt_dofadr[jid]
        # Track door and handle joints
        for name in ("door_hinge", "handle_rot"):
            jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid >= 0:
                self._qadr[name] = m.jnt_qposadr[jid]
                self._vadr[name] = m.jnt_dofadr[jid]

        def gid(n):
            return mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, n)

        self._door_geom = gid("door_g")
        self._handle_geom = gid("handle_g")
        self._arm_geoms = {gid(n) for n in
                           ("base_g", "column_g", "upper_g", "fore_g", "wrist_g")}
        self._door_body = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "door")
        self._handle_site = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "grip_site")

        self._door_start_angle = self.data.qpos[self._qadr.get("door_hinge", 0)] if "door_hinge" in self._qadr else 0.0
        handle_body_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "handle")
        self._handle_start_pos = np.array(self.data.xpos[handle_body_id] if handle_body_id >= 0 else [0, 0, 0])

        self._pose = {"pan": 0.0, "shoulder": 0.0, "elbow": 0.0, "wristp": 0.0}
        self._grip = 0.050
        self._steps = 0
        self._peak_force = 0.0
        self._hold_forces = []
        self._contact_samples = 0
        self._collisions = 0
        self._apply(self._pose, self._grip)
        mujoco.mj_forward(self.model, self.data)

    def _apply(self, pose: dict, grip: float):
        d = self.data
        for name in ARM_JOINTS:
            d.qpos[self._qadr[name]] = pose[name]
            d.qvel[self._vadr[name]] = 0.0
        for name in ("grip_l", "grip_r"):
            d.qpos[self._qadr[name]] = grip
            d.qvel[self._vadr[name]] = 0.0

    def _tick(self, pose: dict, grip: float):
        if self._steps >= self._budget:
            raise BudgetExhausted
        self._apply(pose, grip)
        mujoco.mj_step(self.model, self.data)
        self._apply(pose, grip)
        self._steps += 1
        self._pose, self._grip = pose, grip
        self._door_angle = self.data.qpos[self._qadr.get("door_hinge", 0)] if "door_hinge" in self._qadr else 0.0
        self._handle_angle = self.data.qpos[self._qadr.get("handle_rot", 0)] if "handle_rot" in self._qadr else 0.0
        for i in range(self.data.ncon):
            c = self.data.contact[i]
            if self._door_geom in (c.geom1, c.geom2):
                other = c.geom2 if c.geom1 == self._door_geom else c.geom1
                if other in self._arm_geoms:
                    self._collisions += 1

    def _run(self, target: dict, n: int, grip: float):
        start = dict(self._pose)
        for i in range(1, n + 1):
            self._tick(blend(start, target, i / n), grip)
            if self._collisions:
                return False
        return True

    def _hold(self, n: int, grip: float, sample: bool = False):
        for _ in range(n):
            self._tick(dict(self._pose), grip)
            if sample:
                f = self._grasp_force()
                self._hold_forces.append(f)
                self._peak_force = max(self._peak_force, f)

    def _grasp_force(self):
        f6 = np.zeros(6)
        total = 0.0
        d = self.data
        for i in range(d.ncon):
            c = d.contact[i]
            if self._handle_geom in (c.geom1, c.geom2):
                mujoco.mj_contactForce(self.model, d, i, f6)
                total += abs(float(f6[2]))
        return total

    def _handle_pos(self):
        return np.array(self.data.xpos[mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "handle")])

    def _tip_pos(self):
        return np.array(self.data.site_xpos[self._handle_site])

    def open_door(self, params: dict | None = None):
        from arm_spec import solve
        name, key, scene = resolve_scene(params)

        t0 = time.perf_counter()
        self._build(scene)
        self._budget = scene["budget"]
        handle_start = self._handle_start_pos.copy()
        handle_state = "ungripped"

        def report(success, reason, note=""):
            hold = (sum(self._hold_forces) / len(self._hold_forces)
                    if self._hold_forces else 0.0)
            handle_end = self._handle_start_pos + np.array([
                DOOR_WIDTH * (1 - np.cos(self._door_angle)),
                DOOR_WIDTH * np.sin(self._door_angle),
                0.0
            ])
            return build_metrics(
                engine=ENGINE, obj=name, scene_key=key, stage="full",
                handle_state=handle_state, start_pos=handle_start,
                end_pos=handle_end, hold_force=hold,
                peak_force=self._peak_force,
                contact_samples=self._contact_samples,
                collisions=self._collisions, steps=self._steps,
                budget=self._budget, wall_time=time.perf_counter() - t0,
                door_angle=self._door_angle, note=note)

        hx = scene["door_x"] + DOOR_WIDTH - 0.05
        hy = scene["door_y"]
        hz = scene.get("handle_z", DOOR_HANDLE_HEIGHT)

        # IK targets use wrist position; fingers sit GRIP_MID below wrist.
        # Target wrist at hz + GRIP_MID so finger pads align with handle.
        above = solve(hx, hz + 0.10 + GRIP_MID)
        grip = solve(hx, hz + GRIP_MID)
        # Pull target: move back 0.20m horizontally, keep same height or slightly lower
        pull_end = solve(hx - 0.20, hz - 0.05 + GRIP_MID)

        if above is None or grip is None or pull_end is None:
            return DoorResult(False, "configuration_error", report(False, "configuration_error", "keyframes unsolvable"))

        try:
            # Stage 1: move above handle
            if not self._run(above, 70, 0.050):
                return DoorResult(False, "collision", report(False, "collision", "obstacle during approach"))

            # Stage 2: descend to handle
            if not self._run(grip, 50, 0.050):
                return DoorResult(False, "collision", report(False, "collision", "obstacle during descent"))

            # Stage 3: grip handle
            for i in range(1, 81):
                self._tick(dict(self._pose), aperture_at(i / 80))
                if self._collisions:
                    return DoorResult(False, "collision", report(False, "collision", "obstacle while closing"))
                f = self._grasp_force()
                self._peak_force = max(self._peak_force, f)
                if f > 0.0:
                    self._contact_samples += 1

            force = self._grasp_force()
            if force < GRASP_FORCE_MIN:
                handle_state = "slipped"
                return DoorResult(False, "grasp_failed", report(False, "grasp_failed", f"peak_force={self._peak_force:.3f} N"))
            handle_state = "gripped"

            # Stage 4: pull door open
            if not self._run(pull_end, 100, 0.032):
                if self._door_angle < OPEN_ANGLE_MIN:
                    return DoorResult(False, "stuck", report(False, "stuck", f"door angle only {self._door_angle:.2f} rad"))

            # Stage 5: settle
            self._hold(30, 0.032, sample=True)

        except BudgetExhausted:
            return DoorResult(False, "timeout", report(False, "timeout", f"step budget {self._budget} exhausted"))

        if self._door_angle < OPEN_ANGLE_MIN:
            handle_state = "incomplete"
            return DoorResult(False, "insufficient_open", report(False, "insufficient_open", f"door opened {self._door_angle:.2f} rad"))

        return DoorResult(True, "opened", report(True, "opened", f"door opened {self._door_angle:.2f} rad ({np.degrees(self._door_angle):.1f} deg)"))


__all__ = ["MuJoCoSimulator", "DoorResult"]
