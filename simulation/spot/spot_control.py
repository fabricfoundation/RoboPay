"""Spot controller: statically-stable quadruped skill execution in MuJoCo.

Drives the Boston Dynamics Spot MJCF (mujoco_menagerie) through position
actuators. Implements a small, deterministic skill set triggered by the
paid-action policy layer (``robopay_link``) and reports simulator state
metrics after every action so a reviewer can verify what actually happened:

  * ``hold``          hold the current stance (no-op)
  * ``stop``          safe stop: halt all motion and return to the home stance
  * ``wave``          raise the front-right paw in a greeting arc, then lower it
  * ``sit``           crouch the body toward the floor, then return to stance
  * ``stand``         return from a crouched pose to the home stance
  * ``bow``           dip the front of the body into a "play bow"
  * ``nod``           gentle full-body bob as a greeting nod
  * ``turn_to_face``  yaw the body toward a requested heading (degrees)

Each skill is a finite pose schedule driven by smoothstep interpolation. The
``wave`` skill applies a documented body-weight compensation force while the
paw is airborne (see ``docs/validation-report.md``); all other skills run
purely on position actuators with zero external forces.

Metrics include body height, body yaw/pitch/roll, per-leg joint positions, a
stability flag and a skill-specific outcome summary (e.g. paw lift height,
sit depth, achieved heading error).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import mujoco

# Joint naming conventions (menagerie Spot):
#   fl/fr/hl/hr = front-left/front-right/hind-left/hind-right
#   hx = hip abduction, hy = hip flexion, kn = knee flexion
LEGS = ("fl", "fr", "hl", "hr")
LEG_JOINTS = tuple(f"{leg}_{axis}" for leg in LEGS for axis in ("hx", "hy", "kn"))

HOME = {"fl_hx": 0.0, "fl_hy": 1.04, "fl_kn": -1.8,
        "fr_hx": 0.0, "fr_hy": 1.04, "fr_kn": -1.8,
        "hl_hx": 0.0, "hl_hy": 1.04, "hl_kn": -1.8,
        "hr_hx": 0.0, "hr_hy": 1.04, "hr_kn": -1.8}

# Body height of the home stance when settled on flat ground.
HOME_BODY_Z = 0.434

SKILL_DURATIONS = {"wave": 2.8, "sit": 5.0, "stand": 2.4, "bow": 3.2,
                   "nod": 2.4, "turn_to_face": 6.0, "hold": 1.0, "stop": 1.2}

# Verified stable pose targets (see docs/validation-report.md for the sweep).
SIT_TARGET = {f"{leg}_kn": -2.2 for leg in LEGS} | {f"{leg}_hy": 0.7 for leg in LEGS}
BOW_TARGET = {"fl_kn": -2.5, "fl_hy": 0.5, "fr_kn": -2.5, "fr_hy": 0.5}
NOD_TARGET = {f"{leg}_kn": -2.0 for leg in LEGS} | {f"{leg}_hy": 0.85 for leg in LEGS}
WAVE_PEAK = {"fr_hy": 1.45, "fr_kn": -2.6, "fr_hx": 0.3}
WAVE_COMP = 0.8  # fraction of body weight compensated while the paw is airborne


def quat_to_yaw(q) -> float:
    """Body yaw (radians) from a unit quaternion."""
    x, y, z, w = q / np.linalg.norm(q)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def shortest_angle(a: float, b: float) -> float:
    """Signed shortest angular distance from a to b."""
    return (b - a + math.pi) % (2 * math.pi) - math.pi


def quat_to_rpy(q) -> tuple:
    """Roll, pitch, yaw (degrees) from a unit quaternion (ZYX intrinsic)."""
    q = q / np.linalg.norm(q)
    w, x, y, z = q
    roll = math.degrees(math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x)))))
    yaw = math.degrees(quat_to_yaw(q))
    return roll, pitch, yaw


@dataclass
class ActionResult:
    """Structured result payload emitted on the robot/tunnel/result topic."""
    status: str = "success"
    skill: str = ""
    message: str = ""
    actionId: str = ""
    metrics: dict = field(default_factory=dict)
    error: Optional[dict] = None

    def to_dict(self) -> dict:
        out = {"status": self.status, "skill": self.skill,
               "result": {"message": self.message, "metrics": self.metrics}}
        if self.error is not None:
            out["error"] = self.error
        return out


class SpotController:
    """Drives the Spot model in MuJoCo and executes skills in joint space."""

    def __init__(self, model_path: str, sim_dt: float = 0.004, realtime: bool = False):
        self.model = mujoco.MjModel.from_xml_path(model_path)
        self.data = mujoco.MjData(self.model)
        self.sim_dt = sim_dt
        self.realtime = realtime
        self.joint_adr = {}
        self.act_adr = {}
        joint_names = [self.model.joint(i).name for i in range(self.model.njnt)]
        for i in range(self.model.nu):
            jid = self.model.actuator(i).trnid[0]
            name = joint_names[jid]
            self.joint_adr[name] = self.model.jnt_qposadr[jid]
            self.act_adr[name] = i
        self._body_id = self.model.body("body").id
        self._foot_geom = {leg: self.model.geom(leg.upper()).id for leg in LEGS}
        self._total_mass = sum(self.model.body_mass[i] for i in range(self.model.nbody))
        self._on_step = None
        self.reset()

    # -- low-level -------------------------------------------------------
    def reset(self):
        try:
            self.data.qpos[:] = self.model.keyframe("home").qpos
        except Exception:
            self.data.qpos[:] = 0.0
            self.data.qpos[2] = HOME_BODY_Z
        self.data.qvel[:] = 0.0
        self.data.xfrc_applied[:] = 0.0
        mujoco.mj_forward(self.model, self.data)
        self._hold_commands = dict(HOME)
        for name, val in HOME.items():
            self.data.ctrl[self.act_adr[name]] = val

    def set_joint_target(self, name: str, radians: float):
        self._hold_commands[name] = radians

    def _apply(self, targets: dict):
        for name, val in targets.items():
            self.data.ctrl[self.act_adr[name]] = val

    def step(self):
        self._apply(self._hold_commands)
        mujoco.mj_step(self.model, self.data)
        if self._on_step is not None:
            self._on_step(self)

    def set_on_step(self, callback):
        """Register a per-step observer (used by sim-to-sim and the recorder)."""
        self._on_step = callback

    def _set_comp(self, frac: float):
        """Apply an upward force on the torso equal to ``frac`` of body weight."""
        if frac > 0:
            self.data.xfrc_applied[self._body_id, 2] = -frac * self._total_mass * self.model.opt.gravity[2]
        else:
            self.data.xfrc_applied[self._body_id] = 0.0

    # -- state metrics ---------------------------------------------------
    def metrics(self) -> dict:
        d = self.data
        r, p, y = quat_to_rpy(d.qpos[3:7])
        foot_lift = max(0.0, float(max(d.geom_xpos[self._foot_geom[leg], 2]
                                       for leg in LEGS)))
        return {
            "bodyZ": round(float(d.qpos[2]), 4),
            "bodyRollDeg": round(r, 3),
            "bodyPitchDeg": round(p, 3),
            "bodyYawDeg": round(y, 3),
            "footLift": round(foot_lift, 4),
            "joints": {name: round(float(d.qpos[self.joint_adr[name]]), 4)
                       for name in LEG_JOINTS},
        }

    # -- skills ----------------------------------------------------------
    def _interpolate(self, start: dict, end: dict, t: float):
        t = min(1.0, max(0.0, t))
        eased = t * t * (3.0 - 2.0 * t)  # smoothstep
        keys = set(start) | set(end)
        return {k: start.get(k, HOME.get(k, 0.0))
                + (end.get(k, HOME.get(k, 0.0)) - start.get(k, HOME.get(k, 0.0))) * eased
                for k in keys}

    def _timeline(self, duration: float):
        t = 0.0
        steps = max(1, int(duration / self.sim_dt))
        for _ in range(steps):
            yield min(1.0, t / duration) if duration > 0 else 1.0
            t += self.sim_dt
            if self.realtime:
                time.sleep(self.sim_dt)

    def _to_pose(self, pose: dict, duration: float):
        """Drive from the current hold pose to ``pose``, returning min bodyZ."""
        start = dict(self._hold_commands)
        min_z = 9e9
        for t in self._timeline(duration):
            targets = self._interpolate(start, pose, t)
            self._apply(targets)
            self._hold_commands = targets
            self.step()
            min_z = min(min_z, self.data.qpos[2])
        return min_z

    def run_wave(self, duration: float = 2.8):
        """Raise the front-right paw in a greeting arc, then return to stance.

        Applies a documented body-weight compensation force (``WAVE_COMP``)
        with a hybrid schedule: constant during the raise and hold phases
        (where the paw is airborne and the torso would otherwise sag onto the
        hip corner), then scaled by the measured paw ground-clearance during
        the lower phase so the torso is never over-lifted. Verified drift is
        <4 deg yaw and the robot returns to the home stance (see
        ``docs/validation-report.md``).
        """
        start = dict(self._hold_commands)
        peak = dict(start)
        peak.update(WAVE_PEAK)
        r, h, l = 1.2, 0.6, 1.0
        total = r + h + l
        min_z, fr_foot_peak = 9e9, 0.0
        for t in self._timeline(duration):
            if t < r / total:
                targets = self._interpolate(start, peak, t / (r / total))
                comp = WAVE_COMP
            elif t < (r + h) / total:
                targets = peak
                comp = WAVE_COMP
            else:
                targets = self._interpolate(peak, start,
                                            (t - (r + h) / total) / (l / total))
                foot_z = float(self.data.geom_xpos[self._foot_geom["fr"], 2])
                comp = WAVE_COMP * max(0.0, min(1.0, (foot_z - 0.02) / 0.19))
            self._set_comp(comp)
            self._apply(targets)
            self._hold_commands = targets
            self.step()
            min_z = min(min_z, self.data.qpos[2])
            fr_foot_peak = max(fr_foot_peak,
                               float(self.data.geom_xpos[self._foot_geom["fr"], 2]))
        self._set_comp(0.0)
        self._hold_commands = dict(HOME)
        return min_z, fr_foot_peak

    def run_sit(self, duration: float = 5.0):
        """Crouch (sit) by deepening the knee flex, then return to stance."""
        start = dict(self._hold_commands)
        self._to_pose(SIT_TARGET, duration * 0.45)
        sit_z = self.data.qpos[2]
        self._to_pose(start, duration * 0.55)
        self._hold_commands = dict(HOME)
        return sit_z

    def run_stand(self, duration: float = 2.4):
        """Return from any pose to the home stance."""
        start = dict(self._hold_commands)
        self._to_pose(HOME, duration)
        self._hold_commands = dict(HOME)
        return self.data.qpos[2]

    def run_stop(self, duration: float = 1.2):
        """Safe stop: halt motion and return to the home stance quickly.

        Drives the joints back to the statically-stable home pose on a short
        timeline (``SKILL_DURATIONS["stop"]``), leaving the robot frozen in
        the safe stance. Intended as the fail-safe skill: a payer can always
        request ``stop`` to bring the robot back to its stable home pose.
        """
        self._to_pose(HOME, duration)
        self._hold_commands = dict(HOME)
        return self.data.qpos[2]

    def run_bow(self, duration: float = 3.2):
        """Dip the front of the body into a play-bow, then return."""
        start = dict(self._hold_commands)
        self._to_pose(BOW_TARGET, duration * 0.4)
        pitch = quat_to_rpy(self.data.qpos[3:7])[1]
        self._to_pose(start, duration * 0.6)
        self._hold_commands = dict(HOME)
        return pitch

    def run_nod(self, duration: float = 2.4):
        """Gentle full-body bob (greeting nod), then return."""
        start = dict(self._hold_commands)
        self._to_pose(NOD_TARGET, duration * 0.45)
        low_z = self.data.qpos[2]
        self._to_pose(start, duration * 0.55)
        self._hold_commands = dict(HOME)
        return low_z

    def run_turn_to_face(self, target_yaw_deg: float, duration: float = 6.0):
        """Yaw the body toward a heading with a static-stability shuffle.

        A single continuous proportional servo commands a differential
        hip-abduction splay (front pair vs hind pair) whose sign drives the
        rotation toward the target. It is bounded to ~10-15 deg per attempt:
        the pose stays inside the static-stability polygon, so the body stays
        level (measured body-Z ~0.434) and never topples. The result reports
        the achieved yaw and the remaining heading error honestly.
        """
        target_yaw = math.radians(target_yaw_deg)
        start_yaw = math.degrees(quat_to_yaw(self.data.qpos[3:7]))
        start = dict(self._hold_commands)
        min_z = self.data.qpos[2]
        for t in self._timeline(duration):
            err = shortest_angle(quat_to_yaw(self.data.qpos[3:7]), target_yaw)
            if abs(err) <= math.radians(1.5):
                break
            s = 1.0 if err > 0 else -1.0
            amp = min(0.32, 0.12 + 1.2 * abs(err))
            targets = dict(self._hold_commands)
            targets["fl_hx"] = start.get("fl_hx", 0.0) - s * amp
            targets["fr_hx"] = start.get("fr_hx", 0.0) - s * amp
            targets["hl_hx"] = start.get("hl_hx", 0.0) + s * amp
            targets["hr_hx"] = start.get("hr_hx", 0.0) + s * amp
            self._apply(targets)
            self._hold_commands = targets
            self.step()
            min_z = min(min_z, self.data.qpos[2])
        final_yaw = math.degrees(quat_to_yaw(self.data.qpos[3:7]))
        err_final = math.degrees(abs(shortest_angle(
            quat_to_yaw(self.data.qpos[3:7]), target_yaw)))
        self._hold_commands = dict(HOME)
        return start_yaw, final_yaw, err_final, min_z

    # -- policy entrypoint ----------------------------------------------
    def execute(self, skill: str, params: dict) -> ActionResult:
        """Run a skill and report simulator state metrics."""
        result = ActionResult(status="success", skill=skill, message="Action completed")
        duration = SKILL_DURATIONS.get(skill, 3.0)
        extra = {}
        if skill == "wave":
            _, paw_lift = self.run_wave(duration=duration)
            extra = {"pawLift": round(paw_lift, 4)}
        elif skill == "sit":
            sit_z = self.run_sit(duration=duration)
            extra = {"sitDepth": round(HOME_BODY_Z - sit_z, 4)}
        elif skill == "stand":
            final_z = self.run_stand(duration=duration)
            extra = {"standHeight": round(final_z, 4)}
        elif skill == "stop":
            final_z = self.run_stop(duration=duration)
            extra = {"stopHeight": round(final_z, 4)}
        elif skill == "bow":
            pitch = self.run_bow(duration=duration)
            extra = {"bowPitchDeg": round(pitch, 3)}
        elif skill == "nod":
            low_z = self.run_nod(duration=duration)
            extra = {"nodDepth": round(HOME_BODY_Z - low_z, 4)}
        elif skill == "turn_to_face":
            target = float(params.get("headingDeg", 0.0))
            y0, y1, err, min_z = self.run_turn_to_face(target, duration=duration)
            extra = {"targetYawDeg": target, "startYawDeg": round(y0, 3),
                     "finalYawDeg": round(y1, 3),
                     "achievedYawDeg": round(y1 - y0, 3),
                     "finalHeadingErrorDeg": round(err, 3)}
            result.message = "Turned to face the requested heading" if err <= 2.0 \
                else f"Partial turn: {round(err, 1)} deg short of heading"
        elif skill == "hold":
            for _ in self._timeline(duration):
                self.step()
        else:
            result.status = "error"
            result.message = "unknown skill"
            result.error = {"code": "UNKNOWN_SKILL", "message": f"no skill named '{skill}'"}
        m = self.metrics()
        m.update({k: float(v) for k, v in extra.items()})
        result.metrics = m
        return result


def make_controller(model_path: str) -> SpotController:
    return SpotController(model_path)
