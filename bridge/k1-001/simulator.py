"""k1-001 — MuJoCo backend for active inspection (Tier 1).

Booster K1 is a 22-DoF fixed-base robot with a wrist-mounted camera.
The inspection task: move the camera to three target positions
(left → center → right) and confirm each target is visible within
the camera's field of view.

This simulator uses a simplified 6-DOF serial arm model that captures
the essential kinematics for the inspection trajectory.

Key differentiator from pick-and-stack:
  * Camera-based sensing instead of contact-based grasping.
  * Three sequential inspection targets instead of two physical objects.
  * FOV-centered verification instead of grasp-force verification.

Public surface:
    MuJoCoSimulator().active_inspection(params) -> InspectionResult(success, reason, metrics)
"""
from __future__ import annotations

import time
import math
import mujoco
import numpy as np

from arm_spec import (
    ARM_JOINTS, BudgetExhausted, BASE_H, CAM_FOV, CAM_Z_OFFSET,
    CONFIRM_ANGLE_MAX, CONFIRM_DISTANCE_MAX, DISTANCE_MAX, DISTANCE_MIN,
    KEYFRAMES, LINK1, LINK2, LINK3, MAX_REACH, POSITION_TOLERANCE,
    REACHABILITY_GAP, SCENES, TIMESTEP, InspectionResult,
    STAGE_STEPS, build_metrics, forward, resolve_scene, smoothstep,
)

ENGINE = "mujoco"

# --- inspection scene objects ---
TARGET_SIZE = 0.04       # m, target marker radius
TARGET_MASS = 0.05
TARGET_COLORS = {
    "left":   (0.20, 0.70, 0.45, 1.0),  # green
    "center": (0.20, 0.50, 0.80, 1.0),  # blue
    "right":  (0.80, 0.30, 0.20, 1.0),  # red
}


def _model_xml(targets_config: list) -> str:
    """MJCF with camera-equipped arm and inspection targets."""
    target_bodies = ""
    for tname, ty, tz in targets_config:
        color = TARGET_COLORS.get(tname, (0.5, 0.5, 0.5, 1.0))
        target_bodies += f"""
    <body name="target_{tname}" pos="0 {ty} {tz + TARGET_SIZE}">
      <geom name="target_{tname}_g" type="cylinder"
            size="{TARGET_SIZE} {TARGET_SIZE * 0.3}"
            rgba="{color[0]} {color[1]} {color[2]} {color[3]}"
            contype="2" conaffinity="15"/>
      <site name="target_{tname}_site" pos="0 0 0" size="0.005"
            rgba="1 1 1 0.5"/>
    </body>"""

    return f"""
<mujoco model="k1-001-inspection">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="{TIMESTEP}" gravity="0 0 -9.81" integrator="implicitfast"/>
  <default>
    <joint damping="0.5" armature="0.01"/>
    <geom solref="0.006 1" solimp="0.95 0.99 0.001"/>
  </default>

  <worldbody>
    <light pos="0.5 0 1.8" dir="0 0 -1" diffuse="1 1 1"/>
    <geom name="floor" type="plane" size="2 2 0.05" rgba="0.16 0.18 0.22 1"
          contype="1" conaffinity="15" friction="1.0 0.01 0.001"/>

    <!-- Fixed base plate -->
    <body name="base" pos="0 0 0">
      <geom name="base_g" type="cylinder" size="0.08 0.03" pos="0 0 0.03"
            rgba="0.25 0.27 0.32 1" contype="16" conaffinity="8"/>
      <body name="column" pos="0 0 0.06">
        <joint name="base_rot" type="hinge" axis="0 0 1" range="-3.1416 3.1416"/>
        <geom name="column_g" type="capsule" fromto="0 0 0 0 0 0.15" size="0.04"
              rgba="0.30 0.32 0.38 1" contype="16" conaffinity="8"/>
        <body name="shoulder" pos="0 0 0.15">
          <joint name="shoulder" type="hinge" axis="0 1 0" range="-2.0 2.0"/>
          <geom name="upper_g" type="capsule" fromto="0 0 0 {LINK1} 0 0" size="0.035"
                rgba="0.85 0.55 0.18 1" contype="16" conaffinity="8"/>
          <body name="elbow" pos="{LINK1} 0 0">
            <joint name="elbow" type="hinge" axis="0 1 0" range="-2.6 2.6"/>
            <geom name="fore_g" type="capsule" fromto="0 0 0 {LINK2} 0 0" size="0.030"
                  rgba="0.85 0.55 0.18 1" contype="16" conaffinity="8"/>
            <body name="wrist" pos="{LINK2} 0 0">
              <joint name="wrist_pitch" type="hinge" axis="0 1 0" range="-2.8 2.8"/>
              <joint name="wrist_roll" type="hinge" axis="1 0 0" range="-3.14 3.14"/>
              <geom name="wrist_g" type="box" size="0.035 0.030 0.020"
                    rgba="0.30 0.32 0.38 1" contype="16" conaffinity="8"/>
                <!-- Camera mount -->
              <body name="cam_mount" pos="0 0 -{LINK3}">
                <joint name="cam_pan" type="hinge" axis="0 0 1" range="-1.57 1.57"/>
                <geom name="cam_g" type="box" size="0.025 0.015 0.020"
                      rgba="0.20 0.20 0.25 1" contype="16" conaffinity="8"/>
                <!-- Camera optical center -->
                <site name="cam_site" pos="{CAM_Z_OFFSET} 0 0" size="0.003"
                      rgba="0.9 0.3 0.3 0.6"/>
                <!-- Camera forward direction: points FORWARD along +x local -->
                <site name="cam_axis" pos="{CAM_Z_OFFSET * 2} 0 0" size="0.002"
                      rgba="0.9 0.3 0.3 0.3"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>{target_bodies}
  </worldbody>
</mujoco>
"""


# ------------------------------------------------------------------- simulator --
class MuJoCoSimulator:
    """Inspection simulator for Booster K1.

    Executes a sequential inspection of targets (left → center → right)
    using simplified 6-DOF kinematics with camera FOV verification.
    """

    ROBOT_ID = "k1-001"
    SKILL_ID = "active_inspection"
    ENGINE = ENGINE

    def __init__(self):
        self.model = None
        self.data = None
        self._steps = 0
        self._budget = 500
        self._collisions = 0
        self._confirmations = []  # list of (target_name, success, reason)

    def _build(self, scene: dict):
        targets = scene.get("targets", [])
        xml = _model_xml(targets)
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        m = self.model

        # Joint addresses
        self._qadr, self._vadr = {}, {}
        for name in ARM_JOINTS:
            jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
            self._qadr[name] = m.jnt_qposadr[jid]
            self._vadr[name] = m.jnt_dofadr[jid]

        # Target geoms
        self._target_geoms = {}
        self._target_sites = {}
        for tname, ty, tz in targets:
            gid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, f"target_{tname}_g")
            sid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, f"target_{tname}_site")
            self._target_geoms[tname] = gid
            self._target_sites[tname] = sid

        # Camera site
        self._cam_site = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "cam_site")
        self._cam_axis_site = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "cam_axis")

        # Base and arm geoms (for collision detection)
        self._arm_geoms = {
            mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, n)
            for n in ("base_g", "column_g", "upper_g", "fore_g", "wrist_g", "cam_g")
        }

        # Set initial pose
        self._pose = dict(KEYFRAMES["home"])
        self._steps = 0
        self._apply(self._pose)
        mujoco.mj_forward(self.model, self.data)

    # ---- trajectory ----
    def _apply(self, pose: dict):
        d = self.data
        for name in ARM_JOINTS:
            d.qpos[self._qadr[name]] = pose.get(name, 0.0)
            d.qvel[self._vadr[name]] = 0.0
        self._pose = dict(pose)

    def _tick(self, pose: dict):
        if self._steps >= self._budget:
            raise BudgetExhausted
        self._apply(pose)
        mujoco.mj_step(self.model, self.data)
        mujoco.mj_step(self.model, self.data)  # two steps for stability
        self._steps += 1
        self._pose = dict(pose)
        # Check collisions with targets
        d = self.data
        for i in range(d.ncon):
            c = d.contact[i]
            if any(g in self._arm_geoms for g in (c.geom1, c.geom2)):
                self._collisions += 1
                break

    def _run(self, target_pose: dict, n_steps: int):
        start = dict(self._pose)
        for i in range(1, n_steps + 1):
            u = i / n_steps
            blended = {k: start[k] + (target_pose[k] - start[k]) * smoothstep(u)
                       for k in ARM_JOINTS}
            self._tick(blended)
            if self._collisions:
                return False
        return True

    # ---- sensing ----
    def _camera_pos(self) -> np.ndarray:
        return np.array(self.data.site_xpos[self._cam_site], dtype=float)

    def _camera_axis(self) -> np.ndarray:
        """Direction the camera is pointing, computed from MuJoCo site positions."""
        cam_pos = self.data.site_xpos[self._cam_site]
        axis_pos = self.data.site_xpos[self._cam_axis_site]
        direction = axis_pos - cam_pos
        norm = np.linalg.norm(direction)
        if norm < 1e-9:
            return np.array([0.0, -1.0, 0.0], dtype=float)
        return direction / norm

    def _target_pos(self, target_name: str) -> np.ndarray:
        return np.array(self.data.site_xpos[self._target_sites[target_name]], dtype=float)

    def _inspect_target(self, target_name: str) -> tuple:
        """Check if target is within camera FOV and at valid distance."""
        cam_pos = self._camera_pos()
        cam_axis = self._camera_axis()
        cam_axis = cam_axis / (np.linalg.norm(cam_axis) + 1e-9)

        target_pos = self._target_pos(target_name)
        to_target = target_pos - cam_pos
        dist = np.linalg.norm(to_target)

        if dist < DISTANCE_MIN or dist > DISTANCE_MAX:
            return False, f"distance={dist:.3f}m out of range [{DISTANCE_MIN},{DISTANCE_MAX}]"

        # Check if target is within FOV
        to_target_norm = to_target / (dist + 1e-9)
        angle = np.arccos(np.clip(np.dot(cam_axis, to_target_norm), -1.0, 1.0))

        if angle > CAM_FOV / 2:
            return False, f"target {target_name} outside FOV (angle={angle:.3f}rad)"

        if angle > CONFIRM_ANGLE_MAX:
            return False, f"target {target_name} not centered (angle={angle:.3f}rad)"

        return True, f"confirmed (dist={dist:.3f}m, angle={angle:.3f}rad)"

    # -------------------------------------------------- active_inspection skill
    def active_inspection(self, params: dict | None = None) -> InspectionResult:
        """Execute inspection of all targets in sequence.

        Returns InspectionResult with per-target confirmation details.
        """
        scene = {}
        if params and isinstance(params, dict):
            _dname, _dkey, scene = resolve_scene(params)
        else:
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

        start_cam = self._camera_pos().tolist()
        confirmed_targets = []
        failed_targets = []

        try:
            for tname, ty, tz in targets:
                # Move to target position
                target_key = f"target_{tname}"
                if target_key not in KEYFRAMES:
                    pose = solve_inspection_pose(ty, tz)
                    if pose is None:
                        raise RuntimeError(f"Target {tname} unreachable")
                    KEYFRAMES[target_key] = pose

                above_key = f"above_{tname}"
                if above_key not in KEYFRAMES:
                    pose = dict(KEYFRAMES[target_key])
                    pose["shoulder"] += 0.15
                    KEYFRAMES[above_key] = pose

                # Move above target first
                stage = f"move_to_{tname}"
                if not self._run(KEYFRAMES[above_key], STAGE_STEPS["move_to_target"]):
                    if self._collisions:
                        return InspectionResult(False, "collision", build_metrics(
                            engine=ENGINE, target=tname, scene_key=_dkey, stage=stage,
                            camera_state="collision", start_pos=start_cam,
                            end_pos=self._camera_pos().tolist(),
                            fov_centered=False, distance=0, collisions=self._collisions,
                            steps=self._steps, budget=self._budget,
                            wall_time=time.perf_counter() - t0))

                # Descend to inspection position
                stage = f"descend_{tname}"
                if not self._run(KEYFRAMES[target_key], STAGE_STEPS["move_to_target"] // 2):
                    if self._collisions:
                        return InspectionResult(False, "collision", build_metrics(
                            engine=ENGINE, target=tname, scene_key=_dkey, stage=stage,
                            camera_state="collision", start_pos=start_cam,
                            end_pos=self._camera_pos().tolist(),
                            fov_centered=False, distance=0, collisions=self._collisions,
                            steps=self._steps, budget=self._budget,
                            wall_time=time.perf_counter() - t0))

                # Hold and confirm
                stage = f"confirm_{tname}"
                for _ in range(STAGE_STEPS["hold_centered"]):
                    self._tick(KEYFRAMES[target_key])

                success, reason = self._inspect_target(tname)
                if success:
                    confirmed_targets.append(tname)
                else:
                    failed_targets.append((tname, reason))

        except BudgetExhausted:
            return InspectionResult(False, "timeout", build_metrics(
                engine=ENGINE, target="timeout", scene_key=_dkey, stage="budget_exhausted",
                camera_state="timeout", start_pos=start_cam,
                end_pos=self._camera_pos().tolist(),
                fov_centered=False, distance=0, collisions=self._collisions,
                steps=self._steps, budget=self._budget,
                wall_time=time.perf_counter() - t0))

        # Final result
        all_confirmed = len(confirmed_targets) == len(targets)
        stage = "complete" if all_confirmed else "partial"
        reason = "all_targets_confirmed" if all_confirmed else f"only_{len(confirmed_targets)}_of_{len(targets)}_confirmed"

        return InspectionResult(all_confirmed, reason, build_metrics(
            engine=ENGINE,
            target=",".join(t[0] for t in targets) if targets else "none",
            scene_key=_dkey, stage=stage,
            camera_state="confirmed" if all_confirmed else "partial",
            start_pos=start_cam,
            end_pos=self._camera_pos().tolist(),
            fov_centered=all_confirmed,
            distance=float(np.linalg.norm(
                self._camera_pos() - self._target_pos(targets[0][0])
            )) if targets else 0,
            collisions=self._collisions,
            steps=self._steps, budget=self._budget,
            wall_time=time.perf_counter() - t0,
            extra_targets={
                "confirmedTargets": confirmed_targets,
                "failedTargets": failed_targets,
                "totalTargets": len(targets),
            }))


def solve_inspection_pose(target_y: float, target_z: float = 0.0,
                          approach_angle: float = 0.0) -> dict | None:
    """Compute arm joints to aim camera at a target (module-level helper)."""
    tx = 0.0
    ty = target_y
    tz = BASE_H + target_z

    base_rot = 0.0 if abs(tx) < 1e-6 else math.atan2(ty, tx)
    r = math.sqrt(tx * tx + ty * ty)
    h = tz - BASE_H

    d2 = r * r + h * h
    d = math.sqrt(d2)
    if d > MAX_REACH - 1e-4 or d < abs(LINK1 - LINK2) + 1e-4:
        return None

    cos_e = max(-1.0, min(1.0,
                (d2 - LINK1**2 - LINK2**2) / (2 * LINK1 * LINK2)))
    elbow = math.acos(cos_e)

    phi = math.atan2(h, r) - math.atan2(LINK2 * math.sin(elbow),
                                        LINK1 + LINK2 * math.cos(elbow))
    shoulder = -phi
    arm_angle = math.atan2(h, r)
    wrist_pitch = approach_angle - (shoulder + elbow)

    return {
        "base_rot": base_rot,
        "shoulder": shoulder,
        "elbow": elbow,
        "wrist_pitch": wrist_pitch,
        "wrist_roll": 0.0,
        "cam_pan": 0.0,
    }


__all__ = ["MuJoCoSimulator", "InspectionResult", "KEYFRAMES", "ENGINE"]
