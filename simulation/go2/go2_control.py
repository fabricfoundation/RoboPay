"""Go2 controller: statically-stable quadruped skill execution in MuJoCo.

Drives the Unitree Go2 MJCF (mujoco_menagerie) through torque actuators.
The menagerie Go2 model exposes *motor* actuators (torque units), so the
controller implements a small PD position servo on top of them (the Spot
menagerie model uses native position actuators; Go2 does not, which is why
the control law is explicit here). Implements the same deterministic skill
set triggered by the paid-action policy layer (``robopay_link``) and reports
simulator state metrics after every action:

  * ``hold``          hold the current stance (no-op)
  * ``stop``          safe stop: halt all motion and return to the home stance
  * ``wave``          raise the front-right paw in a greeting arc, then lower it
  * ``sit``           crouch the body toward the floor, then return to stance
  * ``stand``         return from a crouched pose to the home stance
  * ``bow``           dip the front of the body into a "play bow"
  * ``nod``           gentle full-body bob as a greeting nod
  * ``turn_to_face``  yaw the body toward a requested heading (degrees)
  * ``navigate_obstacle`` potential-field obstacle navigation to a goal pose

Each skill is a finite pose schedule driven by smoothstep interpolation. The
``wave`` skill applies a documented body-weight compensation force while the
paw is airborne (see ``docs/validation-report.md``); all other skills run
purely on the joint servo with zero external forces. ``navigate_obstacle``
steers with a potential-field local planner and decides success/failure from
the physics state (goal reached / TIMEOUT / COLLISION), using MuJoCo contact
pairs for obstacle contact — never a distance estimate.

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

from obstacle_world import OBSTACLE_GEOM_PREFIX, OBSTACLES

# Joint naming conventions (menagerie Unitree Go2):
#   FL/FR/RL/RR = front-left/front-right/rear-left/rear-right
#   hip/thigh/calf with explicit suffixes (FL_hip_joint, FL_thigh_joint, ...)
LEGS = ("FL", "FR", "RL", "RR")
LEG_JOINTS = tuple(
    f"{leg}_{part}_joint" for leg in LEGS
    for part in ("hip", "thigh", "calf"))

HOME = {f"{leg}_{part}_joint": val
        for leg in LEGS
        for part, val in (("hip", 0.0), ("thigh", 0.9), ("calf", -1.8))}

# Home body height: settled from the menagerie "home" keyframe
# (freejoint qpos z = 0.27). The controller re-measures it after settling,
# so the acceptance tests compare against the robot's own resting stance.
HOME_BODY_Z = 0.27

SKILL_DURATIONS = {"wave": 2.8, "sit": 5.0, "stand": 2.4, "bow": 3.2,
                    "nod": 2.4, "turn_to_face": 12.0, "hold": 1.0, "stop": 1.2,
                    "navigate_obstacle": 60.0}

# PD position servo gains. Go2 motor torque limits: hip +/-23.7 Nm,
# knee +/-45.43 Nm; MuJoCo clamps ctrl to the actuator range, so large
# initial errors saturate cleanly and settle without overshoot.
KP = 180.0
KD = 8.0

# Verified stable pose targets (see docs/validation-report.md for the sweep).
SIT_TARGET = {f"{leg}_calf_joint": -2.6 for leg in LEGS} \
    | {f"{leg}_thigh_joint": 0.9 for leg in LEGS}
BOW_TARGET = {"FL_calf_joint": -2.5, "FL_thigh_joint": 0.5,
              "FR_calf_joint": -2.5, "FR_thigh_joint": 0.5}
NOD_TARGET = {f"{leg}_calf_joint": -2.0 for leg in LEGS} \
    | {f"{leg}_thigh_joint": 0.85 for leg in LEGS}
WAVE_PEAK = {"FR_thigh_joint": 2.0, "FR_calf_joint": -2.55, "FR_hip_joint": 0.35}
WAVE_COMP = 0.85  # fraction of body weight compensated while the paw is airborne


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


class Go2Controller:
    """Drives the Go2 model in MuJoCo and executes skills in joint space."""

    def __init__(self, model_path: str, sim_dt: float = 0.004,
                 realtime: bool = False):
        self.model = mujoco.MjModel.from_xml_path(model_path)
        self.data = mujoco.MjData(self.model)
        self.sim_dt = sim_dt
        self.realtime = realtime
        self.joint_adr = {}
        self.act_adr = {}
        self.vel_adr = {}
        joint_names = [self.model.joint(i).name for i in range(self.model.njnt)]
        for i in range(self.model.nu):
            jid = self.model.actuator(i).trnid[0]
            name = joint_names[jid]
            self.joint_adr[name] = self.model.jnt_qposadr[jid]
            self.vel_adr[name] = self.model.jnt_dofadr[jid]
            self.act_adr[name] = i
        self._body_id = self.model.body("base").id
        self._foot_geom = {leg: self.model.geom(leg).id for leg in LEGS}
        self._total_mass = sum(self.model.body_mass[i]
                               for i in range(self.model.nbody))
        self._obstacle_geoms = {
            self.model.geom(i).id
            for i in range(self.model.ngeom)
            if self.model.geom(i).name.startswith(OBSTACLE_GEOM_PREFIX)}
        self._on_step = None
        self.home_body_z = HOME_BODY_Z
        self.reset()

    # -- low-level -------------------------------------------------------
    def reset(self, settle: bool = True):
        try:
            self.data.qpos[:] = self.model.keyframe("home").qpos
        except Exception:
            self.data.qpos[:] = 0.0
            self.data.qpos[2] = HOME_BODY_Z
        self.data.qvel[:] = 0.0
        self.data.xfrc_applied[:] = 0.0
        mujoco.mj_forward(self.model, self.data)
        self._hold_commands = dict(HOME)
        if settle:
            for _ in range(int(0.6 / self.sim_dt)):
                self.step()
        self.home_body_z = float(self.data.qpos[2])

    def set_joint_target(self, name: str, radians: float):
        self._hold_commands[name] = radians

    def _apply(self, targets: dict):
        """PD position servo written as motor torques."""
        for name, qdes in targets.items():
            adr = self.act_adr[name]
            q = float(self.data.qpos[self.joint_adr[name]])
            qv = float(self.data.qvel[self.vel_adr[name]])
            self.data.ctrl[adr] = KP * (qdes - q) - KD * qv

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
            self.data.xfrc_applied[self._body_id, 2] = \
                -frac * self._total_mass * self.model.opt.gravity[2]
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

    def collision_count(self) -> int:
        """Number of active MuJoCo contacts involving an obstacle geom.

        Obstacle geoms are discovered from the loaded model by name prefix
        (``obs_*``, injected by ``obstacle_world.build_obstacle_world``). When
        the model has no obstacle geoms this returns 0, so the same controller
        is safe on the plain menagerie scene.
        """
        if not self._obstacle_geoms:
            return 0
        n = 0
        for i in range(self.data.ncon):
            contact = self.data.contact[i]
            if contact.geom1 in self._obstacle_geoms \
                    or contact.geom2 in self._obstacle_geoms:
                n += 1
        return n

    # -- skills ----------------------------------------------------------
    def _interpolate(self, start: dict, end: dict, t: float):
        t = min(1.0, max(0.0, t))
        eased = t * t * (3.0 - 2.0 * t)  # smoothstep
        keys = set(start) | set(end)
        return {k: start.get(k, HOME.get(k, 0.0))
                + (end.get(k, HOME.get(k, 0.0)) - start.get(k, HOME.get(k, 0.0)))
                * eased for k in keys}

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
        the lower phase so the torso is never over-lifted. The robot returns
        to the home stance afterwards (see ``docs/validation-report.md``).
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
                foot_z = float(self.data.geom_xpos[self._foot_geom["FR"], 2])
                comp = WAVE_COMP * max(0.0, min(1.0, (foot_z - 0.02) / 0.19))
            self._set_comp(comp)
            self._apply(targets)
            self._hold_commands = targets
            self.step()
            min_z = min(min_z, self.data.qpos[2])
            fr_foot_peak = max(fr_foot_peak,
                               float(self.data.geom_xpos[self._foot_geom["FR"], 2]))
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

    def run_turn_to_face(self, target_yaw_deg: float, duration: float = 12.0):
        """Yaw the body toward a heading with a static-stability shuffle.

        A single continuous proportional servo commands a differential
        hip-abduction splay (front pair vs hind pair) whose sign drives the
        rotation toward the target. The pose stays inside the static-stability
        polygon, so the body stays level (measured body-Z ~ home) and never
        topples. The controller converges to the target (or times out,
        reporting the residual error honestly); no external torque is applied
        to the torso.
        """
        target_yaw = math.radians(target_yaw_deg)
        start_yaw = math.degrees(quat_to_yaw(self.data.qpos[3:7]))
        start = dict(self._hold_commands)
        min_z = self.data.qpos[2]
        for t in self._timeline(duration):
            err = shortest_angle(quat_to_yaw(self.data.qpos[3:7]),
                                 target_yaw)
            if abs(err) <= math.radians(1.5):
                break
            s = 1.0 if err > 0 else -1.0
            amp = min(0.4, 0.12 + 1.2 * abs(err))
            targets = dict(self._hold_commands)
            targets["FL_hip_joint"] = start.get("FL_hip_joint", 0.0) + s * amp
            targets["FR_hip_joint"] = start.get("FR_hip_joint", 0.0) + s * amp
            targets["RL_hip_joint"] = start.get("RL_hip_joint", 0.0) - s * amp
            targets["RR_hip_joint"] = start.get("RR_hip_joint", 0.0) - s * amp
            self._apply(targets)
            self._hold_commands = targets
            self.step()
            min_z = min(min_z, self.data.qpos[2])
        final_yaw = math.degrees(quat_to_yaw(self.data.qpos[3:7]))
        err_final = math.degrees(abs(shortest_angle(
            quat_to_yaw(self.data.qpos[3:7]), target_yaw)))
        self._hold_commands = dict(HOME)
        return start_yaw, final_yaw, err_final, min_z

    def run_navigate_obstacle(self, goal_x: float, goal_y: float,
                               waypoints: list, duration: float = 60.0):
        """Navigate a static obstacle course to a goal pose.

        Steering is a potential-field local planner: an attractive vector pulls
        toward the current waypoint and each obstacle within its influence
        radius pushes the robot away; the blended direction drives the same
        static-stability hip-abduction shuffle used by ``turn_to_face``, so the
        body stays inside the stability polygon.

        Success is decided from the physics state, never from the loop
        completing:

          * goal reached within tolerance      -> ``success``
          * obstacle contact (MuJoCo contacts) -> ``error`` / ``COLLISION``
          * timeout before the goal            -> ``error`` / ``TIMEOUT``

        ``waypoints`` is a list of ``{"x": .., "y": ..}`` objects (the
        registry contract) or ``(x, y)`` tuples.
        """
        ROBOT_RADIUS = 0.25
        INFLUENCE_M = 0.6
        TOLERANCE_WP = 0.15
        TOLERANCE_GOAL = 0.20
        MAX_SPEED = 0.25

        pts: list = []
        for wp in waypoints:
            if isinstance(wp, dict):
                if "x" not in wp or "y" not in wp:
                    raise ValueError(
                        "each waypoint must be an object with numeric 'x' and 'y'")
                pts.append((float(wp["x"]), float(wp["y"])))
            else:
                pts.append((float(wp[0]), float(wp[1])))
        if not pts:
            pts = [(float(goal_x), float(goal_y))]
        targets = list(pts)
        total_waypoints = len(targets)

        current_wp = 0
        waypoints_reached = 0
        path_length = 0.0
        min_clearance = 9e9
        contacts = 0
        last_x, last_y = self.data.qpos[0], self.data.qpos[1]
        max_steps = max(1, int(duration / self.sim_dt))
        goal_reached = False

        for _ in range(max_steps):
            x, y = self.data.qpos[0], self.data.qpos[1]
            path_length += math.hypot(x - last_x, y - last_y)
            last_x, last_y = x, y

            if current_wp < len(targets):
                wx, wy = targets[current_wp]
                if math.hypot(x - wx, y - wy) <= TOLERANCE_WP:
                    waypoints_reached += 1
                    current_wp += 1

            for ox, oy, r in OBSTACLES:
                d = math.hypot(x - ox, y - oy) - r
                min_clearance = min(min_clearance, d)
            contacts = max(contacts, self.collision_count())

            if current_wp < len(targets):
                tx, ty = targets[current_wp]
            else:
                tx, ty = goal_x, goal_y

            # -- potential field: attraction + repulsion -----------------
            dx, dy = tx - x, ty - y
            dist_t = math.hypot(dx, dy) or 1e-6
            fx, fy = dx / dist_t, dy / dist_t
            for ox, oy, r in OBSTACLES:
                oxx, oyy = x - ox, y - oy
                dist_o = math.hypot(oxx, oyy) or 1e-6
                reach = r + ROBOT_RADIUS + INFLUENCE_M
                if dist_o < reach:
                    strength = (reach - dist_o) / reach
                    fx += strength * oxx / dist_o
                    fy += strength * oyy / dist_o
            norm = math.hypot(fx, fy) or 1e-6
            fx, fy = fx / norm, fy / norm

            target_yaw = math.atan2(fy, fx)
            yaw_error = shortest_angle(quat_to_yaw(self.data.qpos[3:7]),
                                       target_yaw)

            s = 1.0 if yaw_error > 0 else -1.0
            amp = min(0.35, 0.1 + 0.8 * abs(yaw_error))
            targets2 = dict(self._hold_commands)
            targets2["FL_hip_joint"] = targets2.get("FL_hip_joint", 0.0) + s * amp
            targets2["FR_hip_joint"] = targets2.get("FR_hip_joint", 0.0) + s * amp
            targets2["RL_hip_joint"] = targets2.get("RL_hip_joint", 0.0) - s * amp
            targets2["RR_hip_joint"] = targets2.get("RR_hip_joint", 0.0) - s * amp

            forward_vel = min(MAX_SPEED, 0.15 + 0.1 * abs(yaw_error))
            targets2["FL_thigh_joint"] = targets2.get("FL_thigh_joint", 0.9) \
                - forward_vel * 0.5
            targets2["FR_thigh_joint"] = targets2.get("FR_thigh_joint", 0.9) \
                - forward_vel * 0.5
            targets2["RL_thigh_joint"] = targets2.get("RL_thigh_joint", 0.9) \
                + forward_vel * 0.5
            targets2["RR_thigh_joint"] = targets2.get("RR_thigh_joint", 0.9) \
                + forward_vel * 0.5

            self._apply(targets2)
            self._hold_commands = targets2
            self.step()

            if math.hypot(self.data.qpos[0] - goal_x,
                          self.data.qpos[1] - goal_y) <= TOLERANCE_GOAL:
                goal_reached = True
                break

        final_x, final_y = self.data.qpos[0], self.data.qpos[1]
        final_goal_dist = math.hypot(final_x - goal_x, final_y - goal_y)
        final_yaw = quat_to_yaw(self.data.qpos[3:7])
        target_yaw = math.atan2(goal_y - final_y, goal_x - final_x)
        heading_error = abs(shortest_angle(final_yaw, target_yaw))

        result = ActionResult(skill="navigate_obstacle")
        if contacts > 0:
            result.status = "error"
            result.message = "Obstacle contact detected during navigation"
            result.error = {"code": "COLLISION",
                            "message": "the robot contacted an obstacle"}
        elif goal_reached or final_goal_dist <= TOLERANCE_GOAL:
            result.status = "success"
            result.message = "Obstacle navigation completed: goal reached"
        else:
            result.status = "error"
            result.message = "Navigation timed out before reaching the goal"
            result.error = {"code": "TIMEOUT",
                            "message": "goal not reached within the budget"}

        extra = {
            "waypointsReached": waypoints_reached,
            "totalWaypoints": total_waypoints,
            "pathLengthM": round(path_length, 3),
            "minClearanceM": round(min_clearance, 3),
            "contacts": contacts,
            "finalGoalDistanceM": round(final_goal_dist, 3),
            "headingErrorDeg": round(math.degrees(heading_error), 1),
        }
        m = self.metrics()
        m.update({k: float(v) for k, v in extra.items()})
        result.metrics = m
        return result

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
            extra = {"sitDepth": round(self.home_body_z - sit_z, 4)}
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
            extra = {"nodDepth": round(self.home_body_z - low_z, 4)}
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
        elif skill == "navigate_obstacle":
            goal_x = params.get("goalX")
            goal_y = params.get("goalY")
            waypoints = params.get("waypoints")
            if not isinstance(goal_x, (int, float)) \
                    or not isinstance(goal_y, (int, float)):
                result.status = "error"
                result.message = "goalX and goalY are required numeric params"
                result.error = {"code": "INVALID_PARAMS",
                                "message": result.message}
            elif not isinstance(waypoints, list) or len(waypoints) > 8 \
                    or len(waypoints) < 1:
                result.status = "error"
                result.message = "waypoints must be a list of {x, y} (1..8)"
                result.error = {"code": "INVALID_PARAMS",
                                "message": result.message}
            else:
                try:
                    result = self.run_navigate_obstacle(
                        float(goal_x), float(goal_y), waypoints, duration)
                except (TypeError, ValueError, KeyError) as exc:
                    result.status = "error"
                    result.message = f"invalid navigation params: {exc}"
                    result.error = {"code": "INVALID_PARAMS",
                                    "message": result.message}
        else:
            result.status = "error"
            result.message = "unknown skill"
            result.error = {"code": "UNKNOWN_SKILL", "message": f"no skill named '{skill}'"}
        m = self.metrics()
        for k, v in result.metrics.items():
            m.setdefault(k, v)
        m.update({k: float(v) for k, v in extra.items()})
        result.metrics = m
        return result


def make_controller(model_path: str) -> Go2Controller:
    return Go2Controller(model_path)
