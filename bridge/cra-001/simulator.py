"""cra-001 — MuJoCo backend for pick-and-stack (Tier 1).

Key differentiator from pick_object:
  * TWO cubes in the scene (A = pickable, B = stacking base).
  * 7-phase controller: APPROACH → DESCEND → GRIP → LIFT → MOVE_ABOVE_B → PLACE → VERIFY.
  * Cube-to-cube collision enabled so A physically lands on B.
  * Success = A rests on B with verified stack stability.

Public surface:
    MuJoCoSimulator().pick_and_stack(params) -> PickResult(success, reason, metrics)
"""
from __future__ import annotations

import time
import mujoco
import numpy as np

from arm_spec import (
    ARM_JOINTS, BudgetExhausted, CUBE_FRICTION, CUBE_HALF, CUBE_MASS,
    FINGER_CLOSED, FINGER_HALF_X, FINGER_HALF_Z, FINGER_OPEN, GRASP_FORCE_MIN,
    GRASP_WZ, GRIP_MID, KEYFRAMES, LIFT_MIN, LINK1, LINK2, OBSTACLE_HALF_H,
    OBSTACLE_RADIUS, PAD_HALF, PickResult, STAGE_STEPS, TIMESTEP,
    UNREACHABLE_GAP, WORK_R, aperture_at, blend, build_metrics, resolve_scene,
    solve,
)

ENGINE = "mujoco"
STACK_STEPS = {"move_above_b": 80, "place": 60, "verify": 60}

# Cube B position (fixed on table, serves as stacking base)
CUBE_B_POS = (0.28, 0.0)  # xy on table


# ------------------------------------------------------------------- dual-cube model --
def _model_xml(cube_a_xy, cube_b_xy, obstacle_xy) -> str:
    """MJCF with two cubes for stacking.

    Collision bitmasks:
        1 floor   2 cube_A   2 cube_B   4 finger pads   8 obstacle   16 arm links
    Cube_A collides with floor, cube_B, pads, obstacle (conaffinity=15).
    Cube_B collides with floor, cube_A, obstacle (conaffinity=11).
    """
    ax, ay = cube_a_xy
    bx, by = cube_b_xy
    obstacle = ""
    if obstacle_xy is not None:
        ox, oy = obstacle_xy
        obstacle = f"""
    <body name="obstacle" pos="{ox} {oy} 0">
      <geom name="obstacle_g" type="cylinder"
            size="{OBSTACLE_RADIUS} {OBSTACLE_HALF_H}" pos="0 0 {OBSTACLE_HALF_H}"
            rgba="0.80 0.25 0.25 1" contype="8" conaffinity="27"/>
    </body>"""

    return f"""
<mujoco model="cra-001">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="{TIMESTEP}" gravity="0 0 -9.81" integrator="implicitfast"/>
  <default>
    <joint damping="2" armature="0.01"/>
    <geom solref="0.006 1" solimp="0.95 0.99 0.001"/>
  </default>

  <worldbody>
    <light pos="0.4 0 1.6" dir="0 0 -1" diffuse="0.9 0.9 0.9"/>
    <geom name="floor" type="plane" size="2 2 0.05" rgba="0.16 0.18 0.22 1"
          contype="1" conaffinity="15" friction="1.0 0.01 0.001"/>

    <body name="base" pos="0 0 0">
      <geom name="base_g" type="cylinder" size="0.07 0.025" pos="0 0 0.025"
            rgba="0.25 0.27 0.32 1" contype="16" conaffinity="8"/>
      <body name="column" pos="0 0 0.05">
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
                      size="{FINGER_HALF_X} {PAD_HALF} {FINGER_HALF_Z}"
                      rgba="0.90 0.90 0.92 1" contype="4" conaffinity="15"
                      friction="{CUBE_FRICTION} 0.05 0.001"
                      solref="0.02 1" solimp="0.90 0.95 0.001"/>
              </body>
              <body name="finger_r" pos="0 0 -{GRIP_MID}">
                <joint name="grip_r" type="slide" axis="0 -1 0" range="0.012 0.060"/>
                <geom name="finger_r_g" type="box"
                      size="{FINGER_HALF_X} {PAD_HALF} {FINGER_HALF_Z}"
                      rgba="0.90 0.90 0.92 1" contype="4" conaffinity="15"
                      friction="{CUBE_FRICTION} 0.05 0.001"
                      solref="0.02 1" solimp="0.90 0.95 0.001"/>
              </body>
            </body>
          </body>
        </body>
      </body>
    </body>

    <!-- cube A: the one we pick up -->
    <body name="cube_a" pos="{ax} {ay} {CUBE_HALF}">
      <freejoint name="cube_a_free"/>
      <geom name="cube_a_g" type="box" size="{CUBE_HALF} {CUBE_HALF} {CUBE_HALF}"
            mass="{CUBE_MASS}" rgba="0.20 0.70 0.45 1"
            contype="2" conaffinity="15" friction="{CUBE_FRICTION} 0.05 0.001"
            solref="0.02 1" solimp="0.90 0.95 0.001"/>
      <site name="cube_a_site" pos="0 0 0" size="0.006" rgba="0.2 0.9 0.5 0.4"/>
    </body>

    <!-- cube B: stacking base (static on table) -->
    <body name="cube_b" pos="{bx} {by} {CUBE_HALF}">
      <geom name="cube_b_g" type="box" size="{CUBE_HALF} {CUBE_HALF} {CUBE_HALF}"
            mass="{CUBE_MASS}" rgba="0.70 0.40 0.20 1"
            contype="2" conaffinity="11" friction="{CUBE_FRICTION} 0.05 0.001"
            solref="0.02 1" solimp="0.90 0.95 0.001"/>
    </body>{obstacle}
  </worldbody>

  <equality>
    <connect name="grasp" site1="cube_a_site" site2="grip_site" active="false"/>
  </equality>
</mujoco>
"""


# --------------------------------------------------------------- simulator --
class MuJoCoSimulator:
    """One instance == one robot. `pick_and_stack` rebuilds a dual-cube cell
    and executes the full pick → lift → traverse → place → verify pipeline."""

    ROBOT_ID = "cra-001"
    SKILL_ID = "pick_and_stack"
    ENGINE = ENGINE

    def __init__(self):
        self.model = None
        self.data = None
        self._steps = 0
        self._budget = 400  # increased for stack

    def _build(self, scene: dict):
        xml = _model_xml(scene.get("cube_a", scene.get("cube", [0.35, 0.0])),
                         scene.get("cube_b", list(CUBE_B_POS)),
                         scene.get("obstacle"))
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        m = self.model

        def gid(n): return mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, n)
        def bid(n): return mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, n)

        self._qadr, self._vadr = {}, {}
        for name in ARM_JOINTS + ("grip_l", "grip_r"):
            jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
            self._qadr[name] = m.jnt_qposadr[jid]
            self._vadr[name] = m.jnt_dofadr[jid]

        self._cube_a_geom = gid("cube_a_g")
        self._cube_b_geom = gid("cube_b_g")
        self._cube_a_body = bid("cube_a")
        self._cube_b_body = bid("cube_b")
        self._finger_geoms = {gid("finger_l_g"), gid("finger_r_g")}
        self._obs_geom = gid("obstacle_g") if scene.get("obstacle") else -1
        self._arm_geoms = {gid(n) for n in ("base_g", "column_g", "upper_g", "fore_g", "wrist_g")}
        self._grip_site = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "grip_site")
        self._eq_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_EQUALITY, "grasp")

        self._pose = dict(KEYFRAMES["home"])
        self._grip = FINGER_OPEN
        self._steps = 0
        self._peak_force = 0.0
        self._hold_forces = []
        self._contact_samples = 0
        self._collisions = 0
        self._apply(self._pose, self._grip)
        mujoco.mj_forward(self.model, self.data)

    # ---- trajectory ----
    def _apply(self, pose, grip):
        d = self.data
        for name in ARM_JOINTS:
            d.qpos[self._qadr[name]] = pose[name]
            d.qvel[self._vadr[name]] = 0.0
        for name in ("grip_l", "grip_r"):
            d.qpos[self._qadr[name]] = grip
            d.qvel[self._vadr[name]] = 0.0

    def _tick(self, pose, grip):
        if self._steps >= self._budget:
            raise BudgetExhausted
        self._apply(pose, grip)
        mujoco.mj_step(self.model, self.data)
        self._apply(pose, grip)
        self._steps += 1
        self._pose, self._grip = pose, grip
        if self._obs_geom >= 0 and self._obstacle_contact():
            self._collisions += 1

    def _run(self, target, n, grip, abort_on_collision=True):
        start = dict(self._pose)
        for i in range(1, n + 1):
            self._tick(blend(start, target, i / n), grip)
            if abort_on_collision and self._collisions:
                return False
        return True

    def _hold(self, n, grip, sample=False):
        for _ in range(n):
            self._tick(dict(self._pose), grip)
            if sample:
                f, _ = self._grasp_force()
                self._hold_forces.append(f)
                self._peak_force = max(self._peak_force, f)

    # ---- sensing ----
    def _obstacle_contact(self):
        d = self.data
        for i in range(d.ncon):
            c = d.contact[i]
            if self._obs_geom in (c.geom1, c.geom2):
                other = c.geom2 if c.geom1 == self._obs_geom else c.geom1
                if other in self._arm_geoms or other in self._finger_geoms:
                    return True
        return False

    def _grasp_force(self):
        f6 = np.zeros(6)
        total, pads = 0.0, set()
        d = self.data
        for i in range(d.ncon):
            c = d.contact[i]
            if self._cube_a_geom not in (c.geom1, c.geom2):
                continue
            other = c.geom2 if c.geom1 == self._cube_a_geom else c.geom1
            if other in self._finger_geoms:
                mujoco.mj_contactForce(self.model, d, i, f6)
                total += abs(float(f6[0]))
                pads.add(other)
        return total, len(pads)

    def _cube_a_pos(self):
        return [float(v) for v in self.data.xpos[self._cube_a_body]]

    def _cube_b_pos(self):
        return [float(v) for v in self.data.xpos[self._cube_b_body]]

    def _tip_pos(self):
        return np.array(self.data.site_xpos[self._grip_site], dtype=float)

    def _attach(self):
        try:
            self.data.eq_active[self._eq_id] = 1
        except AttributeError:
            self.model.eq_active[self._eq_id] = 1
        mujoco.mj_forward(self.model, self.data)

    def _detach(self):
        try:
            self.data.eq_active[self._eq_id] = 0
        except AttributeError:
            self.model.eq_active[self._eq_id] = 0
        mujoco.mj_forward(self.model, self.data)

    # -------------------------------------------------------- pick_and_stack skill
    def pick_and_stack(self, params: dict | None = None) -> PickResult:
        """7-phase stack controller:
          1. move_above_a  — arm positions above cube A
          2. descend_a     — lower to grasp height
          3. grip_a        — contact-gated finger closure
          4. lift_a        — raise cube A
          5. move_above_b  — traverse to above cube B
          6. place         — descend onto B and release
          7. verify        — confirm A rests on B
        """
        scene = {}
        if params and isinstance(params, dict):
            # Resolve the named object into the physical scene table so
            # `collision` really places an obstacle and `unreachable` really
            # pushes the cube out of the envelope (fixes the template bug
            # where params were used as the scene dict directly).
            _dname, _dkey, scene = resolve_scene(params)
        t0 = time.perf_counter()
        self._build(scene)
        self._budget = scene.get("budget", 500)
        start_a = self._cube_a_pos()
        start_b = self._cube_b_pos()
        grasp_state, stage = "open", "home"

        def report(success, reason, note=""):
            end_a = self._cube_a_pos()
            end_b = self._cube_b_pos()
            stack_stable = (
                success and
                end_a[2] > end_b[2] + 0.02 and  # A above B
                abs(end_a[0] - end_b[0]) < 0.06 and  # XY aligned
                abs(end_a[1] - end_b[1]) < 0.06
            )
            return PickResult(success, reason, build_metrics(
                engine=ENGINE, obj="stack", scene_key="stack", stage=stage,
                grasp_state=grasp_state,
                start_pos=start_a, end_pos=end_a,
                hold_force=(sum(self._hold_forces) / len(self._hold_forces)
                            if self._hold_forces else 0.0),
                peak_force=self._peak_force,
                contact_samples=self._contact_samples,
                collisions=self._collisions, steps=self._steps,
                budget=self._budget, wall_time=time.perf_counter() - t0,
                note=f"{note} | stack_stable={stack_stable} "
                     f"a_z={end_a[2]:.4f} b_z={end_b[2]:.4f}",
                extra={
                    "stackStable": bool(stack_stable),
                    "a_z": round(float(end_a[2]), 4),
                    "b_z": round(float(end_b[2]), 4),
                    "stackOffsetXY": round(
                        float(np.hypot(end_a[0] - end_b[0],
                                       end_a[1] - end_b[1])), 4),
                }))

        # Position targets
        a_xy = np.array([start_a[0], start_a[1]])
        b_xy = np.array([start_b[0], start_b[1]])

        # Envelope check
        if float(np.hypot(a_xy[0], a_xy[1])) > WORK_R + 0.02:
            stage = "stretch"
            return report(False, "unreachable", "cube A out of workspace")

        # Custom keyframes for cube B position
        r_b = float(np.hypot(b_xy[0], b_xy[1]))
        above_b = solve(r_b, GRASP_WZ + 0.14)
        at_b = solve(r_b, GRASP_WZ)

        try:
            # 1/7: MOVE_ABOVE_A
            stage = "move_above_a"
            if not self._run(KEYFRAMES["above"], STAGE_STEPS["move_above"], FINGER_OPEN):
                return report(False, "collision", "obstacle during approach")

            # 2/7: DESCEND_A
            stage = "descend_a"
            if not self._run(KEYFRAMES["grasp"], STAGE_STEPS["descend"], FINGER_OPEN):
                return report(False, "collision", "obstacle during descent")

            # 3/7: GRIP_A — contact-gated closure
            stage = "grip_a"
            n = STAGE_STEPS["grip"]
            for i in range(1, n + 1):
                self._tick(dict(self._pose), aperture_at(i / n))
                if self._collisions:
                    return report(False, "collision", "obstacle during grip")
                f, _ = self._grasp_force()
                if f > 0.0:
                    self._contact_samples += 1
                self._peak_force = max(self._peak_force, f)

            force, pads = self._grasp_force()
            self._peak_force = max(self._peak_force, force)
            if self._peak_force < GRASP_FORCE_MIN or pads < 2:
                grasp_state = "slipped"
                return report(False, "grasp_failed",
                              f"pads={pads} force={self._peak_force:.3f}N")
            self._attach()
            grasp_state = "attached"

            # 4/7: LIFT_A
            stage = "lift_a"
            if not self._run(KEYFRAMES["lift"], STAGE_STEPS["lift"], FINGER_CLOSED):
                return report(False, "collision", "obstacle during lift")

            lifted = self._cube_a_pos()[2] - start_a[2]
            if lifted < LIFT_MIN:
                grasp_state = "slipped"
                return report(False, "grasp_failed", f"rose only {lifted:.3f}m")

            # 5/7: MOVE_ABOVE_B — traverse to above cube B
            stage = "move_above_b"
            if not self._run(above_b, STACK_STEPS["move_above_b"], FINGER_CLOSED):
                return report(False, "collision", "obstacle during traverse")

            # 6/7: PLACE — descend onto B and release
            stage = "place"
            if not self._run(at_b, STACK_STEPS["place"], FINGER_CLOSED):
                return report(False, "collision", "obstacle during placement")
            # Release: open fingers, detach
            grasp_state = "release"
            self._detach()
            for _ in range(10):
                self._tick(dict(self._pose), FINGER_OPEN)

            # 7/7: VERIFY — confirm A rests on B
            stage = "verify"
            self._hold(STACK_STEPS["verify"], FINGER_OPEN, sample=False)

        except BudgetExhausted:
            return report(False, "timeout",
                          f"budget {self._budget} exhausted in {stage}")

        # Stack verification
        end_a = self._cube_a_pos()
        end_b = self._cube_b_pos()
        a_above_b = end_a[2] > end_b[2] + 0.02
        xy_aligned = abs(end_a[0] - end_b[0]) < 0.06 and abs(end_a[1] - end_b[1]) < 0.06

        if not a_above_b or not xy_aligned:
            grasp_state = "off_target"
            return report(False, "stack_failed",
                          f"A_z={end_a[2]:.3f} B_z={end_b[2]:.3f} "
                          f"dx={abs(end_a[0]-end_b[0]):.3f} dy={abs(end_a[1]-end_b[1]):.3f}")

        grasp_state = "stacked"
        return report(True, "stacked",
                      f"cube A stacked on B: A_z={end_a[2]:.3f} > B_z={end_b[2]:.3f}")


__all__ = ["MuJoCoSimulator", "PickResult", "KEYFRAMES", "ENGINE"]
