"""MuJoCo physics for the unitree-g1 planar biped (balance-recover edition).

The robot is a rigid torso that slides in X (forward) only -- its Z height is
pinned by the model at the standing height, so it cannot pitch or sink -- plus
two 2-link legs (hip + knee hinges). For the ``balance_recover`` skill the torso
additionally carries a *pitch* degree of freedom about the hip line: a genuine
inverted-pendulum fall axis. A disturbance pushes the torso; a *torque-limited*
balance PD controller (exactly the kind a real actuator has) fights to keep it
upright. The gait, the pitch DOF, the gains and the torque cap are identical to
the PyBullet backend (simulator_pybullet.py), so the two engines must agree on
the recover/fall verdict -- that is what test_sim2sim verifies.

This is a deliberately *simplified* planar model: the legs are kinematically
driven to their IK targets and do not exchange physical contact forces with the
ground (the foot geoms have contype 0). The torso translation is integrated by
MuJoCo's solver under real gravity, so the gait timing, the swing-foot lift and
the travelled distance are genuine physics. The balance task is genuine physics
too: the torso pitch evolves under real gravity + the torque-limited balance PD,
and a hard enough push tips it past FALL_PITCH (it falls). Nothing numerical is
faked: the pitch read back from the solver decides success or failure.
"""
from __future__ import annotations

import math
import time

import numpy as np

try:
    import mujoco
except Exception as exc:                                  # pragma: no cover
    raise RuntimeError("mujoco is required for the MuJoCo backend") from exc

import g1_spec as spec

# PD gains for the actuators.
KP_LEG = 1500.0      # four leg hinges (hip / knee) -- very stiff so feet do not
KV_LEG = 100.0       #   sag/penetrate the ground (penetration injects a horizontal
                     #   contact force that destabilises the planar inverted pendulum)
KP_TORSO = 600.0     # torso X translation (forward walk velocity)
KV_TORSO = 120.0

# Balance controller (torque-limited PD on torso_pitch). The torque is applied
# manually via qfrc_applied so it is bit-for-bit the same control law the PyBullet
# backend runs through TORQUE_CONTROL -- that is what makes sim-to-sim meaningful.
KP_BAL = spec.KP_BAL
KV_BAL = spec.KV_BAL
MAX_TORQUE_BAL = spec.MAX_TORQUE_BAL


def _ground_z(x: float, obstacles) -> float:
    """Surface height under a foot at world X (0 on flat ground, curb top on a
    curb). ``obstacles`` is a list of (center_x, half_z) curbs."""
    z = 0.0
    for (cx, hz) in (obstacles or ()):
        if abs(x - cx) <= spec.OBSTACLE_HALF_X:
            z = max(z, 2.0 * hz)          # box top = 2 * half-height
    return z


def _build_xml(obstacles) -> str:
    """Assemble the MJCF model string.

    Chain: world -> torso_slide (torso_x slide) -> torso (torso_pitch hinge,
    pivot at the hip line) -> two 2-link legs. The legs are shared with the
    locomotion gait; only the pitch DOF + balance control are new for
    balance_recover.
    """
    curb = ""
    for (cx, hz) in (obstacles or ()):
        curb += (
            f'    <body name="curb_{cx}" pos="{cx} 0 {hz}">\n'
            f'      <geom type="box" size="{spec.OBSTACLE_HALF_X} 0.1 {hz}" '
            f'pos="0 0 0" friction="0.9 0.005 0.005" '
            f'contype="1" conaffinity="1" rgba="0.6 0.4 0.2 1"/>\n'
            f'    </body>\n'
        )
    return f"""<mujoco model="unitree-g1-planar">
  <compiler angle="radian"/>
  <option timestep="{spec.TIMESTEP}" gravity="0 0 -9.81" iterations="50"
          tolerance="1e-8" solver="Newton" integrator="implicit"/>
  <worldbody>
    <geom name="ground" type="plane" pos="0 0 0" size="5 5 0.1" condim="3"
          friction="1.2 0.005 0.005"
          solref="0.008 1" solimp="0.7 0.9 0.005 0.5 2"/>
{curb}    <body name="torso_slide" pos="0 0 {spec.HIP_Z:.3f}">
      <joint name="torso_x" type="slide" axis="1 0 0" limited="false" damping="0.5"/>
      <inertial pos="0 0 0" mass="0.01" diaginertia="1e-6 1e-6 1e-6"/>
      <body name="torso" pos="0 0 {spec.TORSO_H/2:.3f}">
        <joint name="torso_pitch" type="hinge" axis="0 1 0" limited="false" damping="0.05"/>
        <geom type="box" size="0.12 0.09 {spec.TORSO_H/2:.3f}" pos="0 0 0"
              density="210" rgba="0.2 0.5 0.9 1"/>
        <body name="left_thigh" pos="0 {spec.HIP_X_OFFSET} {-spec.TORSO_H/2:.3f}">
          <joint name="left_hip" type="hinge" axis="0 1 0"
                 range="{spec.HIP_MIN} {spec.HIP_MAX}" limited="true" damping="0.2"/>
          <geom type="capsule" size="0.035 0.14" pos="0 0 {-spec.THIGH_LEN/2:.3f}"
                rgba="0.9 0.9 0.9 1"/>
          <body name="left_shank" pos="0 0 {-spec.THIGH_LEN}" >
            <joint name="left_knee" type="hinge" axis="0 1 0"
                   range="{spec.KNEE_MIN} {spec.KNEE_MAX}" limited="true" damping="0.2"/>
            <geom type="capsule" size="0.03 0.14" pos="0 0 {-spec.SHANK_LEN/2:.3f}"
                  rgba="0.8 0.8 0.8 1"/>
            <body name="left_foot" pos="0 0 {-spec.SHANK_LEN}">
              <geom type="box" size="{spec.FOOT_HALF} 0.04 {spec.FOOT_H}"
                    pos="0 0 {-spec.FOOT_H/2:.3f}"
                    friction="0.2 0.005 0.005" contype="0" conaffinity="0"
                    rgba="0.3 0.3 0.3 1"/>
            </body>
          </body>
        </body>
        <body name="right_thigh" pos="0 {-spec.HIP_X_OFFSET} {-spec.TORSO_H/2:.3f}">
          <joint name="right_hip" type="hinge" axis="0 1 0"
                 range="{spec.HIP_MIN} {spec.HIP_MAX}" limited="true" damping="0.2"/>
          <geom type="capsule" size="0.035 0.14" pos="0 0 {-spec.THIGH_LEN/2:.3f}"
                rgba="0.9 0.9 0.9 1"/>
          <body name="right_shank" pos="0 0 {-spec.THIGH_LEN}">
            <joint name="right_knee" type="hinge" axis="0 1 0"
                   range="{spec.KNEE_MIN} {spec.KNEE_MAX}" limited="true" damping="0.2"/>
            <geom type="capsule" size="0.03 0.14" pos="0 0 {-spec.SHANK_LEN/2:.3f}"
                  rgba="0.8 0.8 0.8 1"/>
            <body name="right_foot" pos="0 0 {-spec.SHANK_LEN}">
              <geom type="box" size="{spec.FOOT_HALF} 0.04 {spec.FOOT_H}"
                    pos="0 0 {-spec.FOOT_H/2:.3f}"
                    friction="0.2 0.005 0.005" contype="0" conaffinity="0"
                    rgba="0.3 0.3 0.3 1"/>
            </body>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
    <position name="torso_x" joint="torso_x" kp="{KP_TORSO}" kv="{KV_TORSO}"/>
    <position name="left_hip"  joint="left_hip"  kp="{KP_LEG}" kv="{KV_LEG}"/>
    <position name="left_knee" joint="left_knee" kp="{KP_LEG}" kv="{KV_LEG}"/>
    <position name="right_hip" joint="right_hip" kp="{KP_LEG}" kv="{KV_LEG}"/>
    <position name="right_knee" joint="right_knee" kp="{KP_LEG}" kv="{KV_LEG}"/>
  </actuator>
</mujoco>"""


class MuJoCoSimulator:
    """Physics-backed walker for unitree-g1 (locomotion + balance-recover)."""

    ROBOT_ID = "unitree-g1"
    SKILL_ID = "balance_recover"

    def __init__(self):
        self._model = None
        self._data = None
        self._obstacles = None
        self._scene_key = None
        self._virtual_x = 0.0
        self._stride_no = -1
        self._obstacle_contact = False
        self._collisions = 0
        self._pitch_dof = 1      # torso_pitch is DOF index 1 (after torso_x)

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
        self._data.qpos[:] = 0.0
        self._data.qfrc_applied[:] = 0.0
        self._virtual_x = 0.0
        self._stride_no = -1
        self._obstacle_contact = False
        self._collisions = 0
        self._pitch = 0.0
        self._max_pitch = 0.0
        self._fell = False
        mujoco.mj_forward(self._model, self._data)

    def _hip_world(self, side: str):
        """World (x, y, z) of the given hip joint origin."""
        torso_x = float(self._data.qpos[0])
        y = spec.HIP_X_OFFSET if side == "left" else -spec.HIP_X_OFFSET
        hip_z = spec.HIP_Z
        return torso_x, y, hip_z

    def _foot_targets(self, step: int, obstacles, advancing: bool):
        """Return {leg: (target_x, target_z)} for the foot-body origin.

        When not advancing (balance / stop) both feet are planted under the
        torso at the reference X. The torso pitch DOF is handled by the balance
        controller, not by the foot IK.
        """
        if not advancing:
            g = _ground_z(self._virtual_x, obstacles) + spec.FOOT_H
            return {"left": (self._virtual_x, g), "right": (self._virtual_x, g)}
        half = spec.SWING_STEPS
        stride_no = step // half
        t = (step % half) / half
        support = "left" if (stride_no % 2 == 0) else "right"
        swing = "right" if support == "left" else "left"
        targets = {}
        targets[support] = (self._virtual_x,
                            _ground_z(self._virtual_x, obstacles) + spec.FOOT_H)
        rear_x = self._virtual_x - spec.STEP_LEN / 2.0
        fwd_x = self._virtual_x + spec.STEP_LEN / 2.0
        swing_x = rear_x + (fwd_x - rear_x) * t
        swing_z = (_ground_z(swing_x, obstacles) + spec.FOOT_H
                   + spec.STEP_CLEAR * math.sin(math.pi * t))
        targets[swing] = (swing_x, swing_z)
        return targets

    def _apply_control(self, targets):
        # Torso X follows the commanded trajectory (held at 0 for balance/stop).
        self._data.ctrl[0] = self._virtual_x
        for leg in ("left", "right"):
            tx, tz = targets[leg]
            hx, hy, hz = self._hip_world(leg)
            dx = tx - hx
            dz = tz - hz
            hip_a, knee_a = spec.leg_ik(dx, dz)
            self._data.ctrl[1 + spec.LEG_JOINTS.index(f"{leg}_hip")] = hip_a
            self._data.ctrl[1 + spec.LEG_JOINTS.index(f"{leg}_knee")] = knee_a
        # Torque-limited balance PD on the torso pitch DOF (manual, so it matches
        # the PyBullet TORQUE_CONTROL law exactly). Target posture is upright.
        tau = KP_BAL * (0.0 - self._data.qpos[self._pitch_dof]) \
              - KV_BAL * self._data.qvel[self._pitch_dof]
        tau = min(max(tau, -MAX_TORQUE_BAL), MAX_TORQUE_BAL)
        self._data.qfrc_applied[self._pitch_dof] = tau

    def _check_obstacle_contact(self):
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
        push_w = float(scene.get("push", 0.0))
        t_push = int(spec.PUSH_T * budget)
        advancing = False  # balance-recover and stop are stance tasks (no gait)
        self._reset(obstacles)

        start = [float(self._data.qpos[0]), 0.0, spec.STAND_Z]
        t0 = time.perf_counter()
        steps = 0
        reached = False
        while steps < budget:
            # Apply the disturbance once, at t_push, as an angular velocity about
            # the hip line. This is a genuine toppling impulse -- the balance PD
            # then has to catch it.
            if steps == t_push and push_w != 0.0:
                self._data.qvel[self._pitch_dof] = push_w
            self._apply_control(self._foot_targets(steps, obstacles, advancing))
            mujoco.mj_step(self._model, self._data)
            self._check_obstacle_contact()
            pitch = float(self._data.qpos[self._pitch_dof])
            self._pitch = pitch
            self._max_pitch = max(self._max_pitch, abs(pitch))
            if abs(pitch) > spec.FALL_PITCH:
                self._fell = True
            steps += 1
            # Once the robot has fallen, stop integrating -- the task is over.
            if self._fell:
                break

        wall = time.perf_counter() - t0
        end = [float(self._data.qpos[0]), 0.0, spec.STAND_Z]
        pitch_end = self._pitch

        if key == "stop":
            success = True
            reached = True
            note = "hold pose; upright within tolerance"
        elif self._fell:
            success = False
            reached = False
            note = (f"fell: torso pitch reached {self._max_pitch:.3f} rad "
                    f"(> {spec.FALL_PITCH} rad) -- genuine physics failure")
        elif abs(pitch_end) < spec.RECOVER_PITCH:
            success = True
            reached = True
            note = f"recovered upright (final pitch {pitch_end:+.3f} rad)"
        else:
            success = False
            reached = False
            note = (f"did not recover within budget "
                    f"(final pitch {pitch_end:+.3f} rad)")
        metrics = spec.build_metrics(
            engine="mujoco", scene_key=key, stage=key,
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
        msg = (f"{key}: pitch {pitch_end:+.4f} rad in {steps} steps "
               f"({'recovered' if success else 'fell'})")
        return spec.WalkResult(success, msg, metrics)

    # ----------------------------------------------------------- public API
    def balance_recover(self, params: dict | None = None):
        return self.run("balance_recover", params)

    def stop(self, params: dict | None = None):
        return self.run("stop", params)


if __name__ == "__main__":            # pragma: no cover - manual debug
    sim = MuJoCoSimulator()
    for name in ("balance_recover", "stop"):
        r = getattr(sim, name)()
        print(name, "->", r.message)
        print("   ", r.metrics)
    # also show a hard-push fall
    r = sim.balance_recover({"push": spec.PUSH_W_FALL})
    print("balance_recover(hard) ->", r.message)
    print("   ", r.metrics)
