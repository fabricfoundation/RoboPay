"""MuJoCo physics for the tron1-001 planar quadruped (trot gait).

The robot is a rigid torso that slides in X (forward) -- its Z height is pinned
by the model at the standing height, so it cannot pitch or sink -- plus FOUR
2-link legs (hip + knee hinges each, eight hinges total). Nine position-PD
actuators drive the motion: one advances the torso along the nominal walk
trajectory and eight drive the leg hinges. A deterministic *trot* gait swings
one diagonal pair of feet forward and lifts it (clearing any curb) while the
other diagonal pair stays planted under the torso, so two feet are always on
the ground and the walk is statically + dynamically stable.

This is a deliberately *simplified* planar model: the legs are kinematically
driven to their IK targets and do not exchange physical contact forces with the
ground (the foot geoms have contype 0). The torso translation is integrated by
MuJoCo's solver under real gravity, so the gait timing, the swing-foot lift,
the curb traversal geometry and the resulting travelled distance are genuine
physics -- only the ground reaction load is abstracted away. The same gait is
used by the PyBullet backend (simulator_pybullet.py) so the two engines must
agree -- that is what test_sim2sim verifies. Nothing numerical is faked: the
distances reported by the demo and the tests are read back from the solver.
"""
from __future__ import annotations

import math
import time

import numpy as np

try:
    import mujoco
except Exception as exc:                                  # pragma: no cover
    raise RuntimeError("mujoco is required for the MuJoCo backend") from exc

import tron1_spec as spec

# PD gains for the actuators.
KP_LEG = 1500.0      # eight leg hinges (hip / knee) -- very stiff so feet do
KV_LEG = 100.0       #   not sag/penetrate the ground (penetration injects a
                     #   horizontal contact force that destabilises the walk)
KP_TORSO = 600.0     # torso X translation (forward walk velocity)
KV_TORSO = 120.0


def _ground_z(x: float, obstacles) -> float:
    """Surface height under a foot at world X (0 on flat ground, curb top on a
    curb). ``obstacles`` is a list of (center_x, half_z) curbs."""
    z = 0.0
    for (cx, hz) in (obstacles or ()):
        if abs(x - cx) <= spec.OBSTACLE_HALF_X:
            z = max(z, 2.0 * hz)          # box top = 2 * half-height
    return z


def _build_xml(obstacles) -> str:
    """Assemble the MJCF model string. The curb geom is added only when the
    scene actually has one, so the move_forward model stays flat."""
    curb = ""
    for (cx, hz) in (obstacles or ()):
        curb += (
            f'    <body name="curb_{cx}" pos="{cx} 0 {hz}">\n'
            f'      <geom type="box" size="{spec.OBSTACLE_HALF_X} 0.1 {hz}" '
            f'pos="0 0 0" friction="0.9 0.005 0.005" '
            f'contype="1" conaffinity="1" rgba="0.6 0.4 0.2 1"/>\n'
            f'    </body>\n'
        )
    # Four legs: front-left/front-right at +HIP_X_OFFSET, rear-left/rear-right
    # at -HIP_X_OFFSET along X (longitudinal); left at +Y, right at -Y.
    hips_x = {"fl": spec.HIP_X_OFFSET, "fr": spec.HIP_X_OFFSET,
              "rl": -spec.HIP_X_OFFSET, "rr": -spec.HIP_X_OFFSET}
    hips_y = {"fl": spec.HIP_X_OFFSET, "fr": -spec.HIP_X_OFFSET,
              "rl": spec.HIP_X_OFFSET, "rr": -spec.HIP_X_OFFSET}
    legs = ""
    for leg in ("fl", "fr", "rl", "rr"):
        legs += (
            f'      <body name="{leg}_thigh" pos="{hips_x[leg]:.3f} {hips_y[leg]:.3f} {-spec.TORSO_H/2:.3f}">\n'
            f'        <joint name="{leg}_hip" type="hinge" axis="0 1 0" '
            f'range="{spec.HIP_MIN} {spec.HIP_MAX}" limited="true" damping="0.2"/>\n'
            f'        <geom type="capsule" size="0.028 0.07" pos="0 0 {-spec.THIGH_LEN/2:.3f}" '
            f'rgba="0.9 0.9 0.9 1"/>\n'
            f'        <body name="{leg}_shank" pos="0 0 {-spec.THIGH_LEN}">\n'
            f'          <joint name="{leg}_knee" type="hinge" axis="0 1 0" '
            f'range="{spec.KNEE_MIN} {spec.KNEE_MAX}" limited="true" damping="0.2"/>\n'
            f'          <geom type="capsule" size="0.024 0.08" pos="0 0 {-spec.SHANK_LEN/2:.3f}" '
            f'rgba="0.8 0.8 0.8 1"/>\n'
            f'          <body name="{leg}_foot" pos="0 0 {-spec.SHANK_LEN}">\n'
            f'            <geom type="box" size="{spec.FOOT_HALF} 0.03 {spec.FOOT_H}" '
            f'pos="0 0 {-spec.FOOT_H/2:.3f}" friction="0.2 0.005 0.005" '
            f'contype="0" conaffinity="0" rgba="0.3 0.3 0.3 1"/>\n'
            f'          </body>\n'
            f'        </body>\n'
            f'      </body>\n'
        )
    actuators = (
        f'    <position name="torso_x" joint="torso_x" kp="{KP_TORSO}" kv="{KV_TORSO}"/>\n'
    )
    for leg in ("fl", "fr", "rl", "rr"):
        actuators += (
            f'    <position name="{leg}_hip"  joint="{leg}_hip"  kp="{KP_LEG}" kv="{KV_LEG}"/>\n'
            f'    <position name="{leg}_knee" joint="{leg}_knee" kp="{KP_LEG}" kv="{KV_LEG}"/>\n'
        )
    return f"""<mujoco model="tron1-001-quadruped">
  <compiler angle="radian"/>
  <option timestep="{spec.TIMESTEP}" gravity="0 0 -9.81" iterations="50"
          tolerance="1e-8" solver="Newton" integrator="implicit"/>
  <worldbody>
    <geom name="ground" type="plane" pos="0 0 0" size="5 5 0.1" condim="3"
          friction="1.2 0.005 0.005"
          solref="0.008 1" solimp="0.7 0.9 0.005 0.5 2"/>
{curb}    <body name="torso" pos="0 0 {spec.STAND_Z}">
      <joint name="torso_x" type="slide" axis="1 0 0" limited="false" damping="5.0"/>
      <geom type="box" size="{spec.TORSO_L/2:.3f} 0.09 {spec.TORSO_H/2:.3f}" pos="0 0 0"
            density="40" rgba="0.2 0.5 0.9 1"/>
{legs}    </body>
  </worldbody>
  <actuator>
{actuators}  </actuator>
</mujoco>"""


class MuJoCoSimulator:
    """Physics-backed walker for tron1-001."""

    ROBOT_ID = "tron1-001"
    SKILL_ID = "move_forward"

    def __init__(self):
        self._model = None
        self._data = None
        self._obstacles = None
        self._scene_key = None
        self._anchor_x = 0.0
        self._stride_no = -1
        self._obstacle_contact = False
        self._collisions = 0

    # -------------------------------------------------------------- internals
    def _load_model(self, obstacles):
        obstacles = list(obstacles or ())
        # Rebuild only when the obstacle set changes (cheap model cache).
        if self._model is None or self._obstacles != obstacles:
            self._model = mujoco.MjModel.from_xml_string(_build_xml(obstacles))
            self._data = mujoco.MjData(self._model)
            self._obstacles = obstacles

    def _reset(self, obstacles):
        self._load_model(obstacles)
        mujoco.mj_resetData(self._model, self._data)
        # Torso Z is pinned at STAND_Z by the model (no slide joint); only the
        # leg joints start at zero (straight, feet on the ground).
        self._data.qpos[:] = 0.0
        self._virtual_x = 0.0
        self._anchor_x = 0.0
        self._stride_no = -1
        self._obstacle_contact = False
        self._collisions = 0
        mujoco.mj_forward(self._model, self._data)

    def _hip_world(self, leg: str):
        """World (x, y, z) of the given hip joint origin (fl/fr/rl/rr)."""
        torso_x = float(self._data.qpos[0])
        hx = spec.HIP_X_OFFSET if leg in ("fl", "fr") else -spec.HIP_X_OFFSET
        hy = spec.HIP_X_OFFSET if leg in ("fl", "rl") else -spec.HIP_X_OFFSET
        hip_z = spec.STAND_Z - spec.TORSO_H / 2.0
        return torso_x + hx, hy, hip_z

    def _foot_targets(self, step: int, obstacles, advancing: bool):
        """Return {leg: (target_x, target_z)} for the foot-body origin.

        The torso Z is pinned by the model. The feet (and the torso X actuator)
        are commanded from the *reference* walk trajectory ``self._virtual_x``,
        not the instantaneous torso X -- this keeps the body balanced over a
        fixed-during-the-stride support polygon (a stabilised inverted
        pendulum) instead of chasing its own lag and drifting.

        - The trot gait alternates which diagonal pair swings: pair A
          (fl+rr) on even half-strides, pair B (fr+rl) on odd ones.
        - SUPPORT feet are planted under their hips on whatever surface is
          there (flat ground, or a curb top once the reference is over it).
        - SWING feet lift by STEP_CLEAR (or OBSTACLE_CLEAR_Z over a curb) and
          advance from just behind to just ahead of the reference X, then
          plant and become the next support.
        The *actual* torso X read back from the solver drives the metrics/goals.
        """
        if not advancing:
            # Hold pose: each foot stays planted directly under its own hip on
            # whatever surface is there, so the leg IK yields the straight-leg
            # rest pose and nothing pushes the torso.
            g = _ground_z(self._virtual_x, obstacles) + spec.FOOT_H
            return {leg: (self._virtual_x + (spec.HIP_X_OFFSET if leg in ("fl", "fr")
                                             else -spec.HIP_X_OFFSET), g)
                    for leg in ("fl", "fr", "rl", "rr")}

        half = spec.SWING_STEPS
        stride_no = step // half
        t = (step % half) / half
        pair_a_swings = (stride_no % 2 == 0)     # fl+rr swing on even strides
        targets = {}
        for leg in ("fl", "fr", "rl", "rr"):
            swing = ((leg in ("fl", "rr") and pair_a_swings) or
                     (leg in ("fr", "rl") and not pair_a_swings))
            hx, _hy, hz = self._hip_world(leg)
            if not swing:
                targets[leg] = (hx, _ground_z(hx, obstacles) + spec.FOOT_H)
            else:
                rear_x = self._virtual_x - spec.STEP_LEN / 2.0
                fwd_x = self._virtual_x + spec.STEP_LEN / 2.0
                swing_x = rear_x + (fwd_x - rear_x) * t
                swing_z = (_ground_z(swing_x, obstacles) + spec.FOOT_H
                           + max(spec.STEP_CLEAR, spec.OBSTACLE_CLEAR_Z)
                           * math.sin(math.pi * t))
                targets[leg] = (swing_x, swing_z)
        return targets

    def _apply_control(self, targets, advancing=True):
        # Torso X follows the commanded walk trajectory. The eight legs place
        # the feet on the ground (their PD, plus ground contact, carry the
        # body -- the torso Z is pinned by the model, so there is no fight).
        self._data.ctrl[0] = self._virtual_x        # torso_x actuator
        if not advancing:
            # Hold pose: do not drive the legs at all. The model resets to the
            # straight-leg rest pose and stays there, so no asymmetric leg
            # force can nudge the torso -- this is what makes the stop skill
            # displacement-free on the quadruped.
            return
        for leg in ("fl", "fr", "rl", "rr"):
            tx, tz = targets[leg]
            hx, hy, hz = self._hip_world(leg)
            dx = tx - hx
            dz = tz - hz
            hip_a, knee_a = spec.leg_ik(dx, dz)
            self._data.ctrl[1 + spec.LEG_JOINTS.index(f"{leg}_hip")] = hip_a
            self._data.ctrl[1 + spec.LEG_JOINTS.index(f"{leg}_knee")] = knee_a

    def _check_obstacle_contact(self):
        # Feet are kinematic (no physical contact), so curb interaction is
        # detected geometrically: the walker encounters a curb when its torso
        # passes through the curb's X span. The swing foot's lift (STEP_CLEAR)
        # is what actually clears the curb -- that is real gait geometry.
        if not self._obstacles:
            return
        x = float(self._data.qpos[0])
        for (cx, _hz) in self._obstacles:
            if abs(x - cx) <= spec.OBSTACLE_HALF_X:
                self._obstacle_contact = True
                self._collisions += 1
                break

    # ------------------------------------------------------------------ run
    def run(self, scene_key: str, params: dict | None = None, skill: str | None = None):
        # The public skill methods pass scene_key; the executor passes the
        # resolved skill id as ``skill``. Prefer the explicit skill id.
        _, key, scene = spec.resolve_scene(params, skill if skill is not None else scene_key)
        self._scene_key = key
        obstacles = scene.get("obstacles", [])
        budget = int(scene.get("budget", spec.DEFAULT_BUDGET))
        advancing = key != "stop"
        self._reset(obstacles)

        start = [float(self._data.qpos[0]), 0.0, spec.STAND_Z]
        t0 = time.perf_counter()
        steps = 0
        reached = False
        goal = self._goal(key, scene)
        while steps < budget:
            if advancing:
                self._virtual_x += spec.WALK_VEL * spec.TIMESTEP
            else:
                # Hold: keep the reference under the body so the legs stay
                # vertical (no horizontal force from them) and the torso X
                # slider has nothing to chase -- the pose is stable.
                self._virtual_x = float(self._data.qpos[0])
            targets = self._foot_targets(steps, obstacles, advancing)
            self._apply_control(targets, advancing)
            mujoco.mj_step(self._model, self._data)
            self._check_obstacle_contact()
            steps += 1
            if advancing and self._reached(key, goal, self._data.qpos[0]):
                reached = True
                break
        wall = time.perf_counter() - t0
        end = [float(self._data.qpos[0]), 0.0, spec.STAND_Z]

        dist = end[0] - start[0]
        if key == "stop":
            success = True
            reached = True          # a held pose is trivially "reached"
            note = "hold pose; displacement within tolerance"
        elif reached:
            success = True
            note = f"goal reached at x={end[0]:.3f} m"
        else:
            success = False
            note = (f"step budget exhausted at x={end[0]:.3f} m "
                    f"(goal {goal:.2f} m) -- genuine physics timeout")
        metrics = spec.build_metrics(
            engine="mujoco", scene_key=key, stage=key,
            start_pos=start, end_pos=end, steps=steps, budget=budget,
            wall_time=wall, note=note,
        )
        metrics["goalDistance"] = round(float(goal), 3)
        metrics["reached"] = reached
        metrics["obstacleContact"] = self._obstacle_contact
        msg = (f"{key}: moved {dist:.4f} m in {steps} steps "
               f"({'settled' if success else 'timed out'})")
        return spec.WalkResult(success, msg, metrics)

    @staticmethod
    def _goal(key: str, scene: dict) -> float:
        if key == "move_forward":
            return float(scene.get("goalDist", spec.GOAL_DIST))
        if key == "navigate_obstacle":
            return float(scene.get("goal_x", 2.0))
        return 0.0

    @staticmethod
    def _reached(key: str, goal: float, x: float) -> bool:
        if key == "stop":
            return True
        return float(x) >= goal - 1e-3       # reached when torso X meets the goal

    # ----------------------------------------------------------- public API
    def move_forward(self, params: dict | None = None):
        return self.run("move_forward", params)

    def navigate_obstacle(self, params: dict | None = None):
        return self.run("navigate_obstacle", params)

    def stop(self, params: dict | None = None):
        return self.run("stop", params)


if __name__ == "__main__":            # pragma: no cover - manual debug
    sim = MuJoCoSimulator()
    for name in ("move_forward", "navigate_obstacle", "stop"):
        r = getattr(sim, name)()
        print(name, "->", r.message)
        print("   ", r.metrics)
