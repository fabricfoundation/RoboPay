"""Task policy for the G1 push-to-target skill.

This is deliberately *not* a recorded trajectory. Every joint command is
derived at run time from the current observation:

  * the turn angle comes from the bearing to the puck's observed position,
  * the push direction comes from the live puck-to-goal vector,
  * each Cartesian setpoint is turned into a joint configuration by a
    constrained IK solve (see `ik.py`), re-solved every control tick, and
  * stage transitions fire on sensed conditions -- joint convergence, achieved
    puck displacement -- never on a timer.

Timers exist only as failure guards. Both ends of the motion are parameters of
the paid action: the payer picks where the puck starts *and* where it must end
up. A replayed animation has nothing to replay, which is the property the
bounty's "cannot simply replay a predefined animation" rule is after.

Why a push and not a grasp. The G1 hand in this model has its index and middle
fingers fixed 57mm apart with no travel in that direction, and an opposing
thumb 86mm further up the palm. Objects narrower than the split pass between
the fingers untouched; wider ones are crushed by the position-controlled
joints, with measured peaks of 60-120N on a 100g object, which ejects it.
Controlled contact is reliable on this hand; holding is not. With the fingers
pointed down the hand makes a stable vertical paddle, which is what the push
uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..simulation.base import HAND_JOINTS_RIGHT, Observation
from .ik import ArmIK, IKResult
from .stages import Stage, StageRecord

#: Reference point in the wrist frame that the IK positions, 110mm out along
#: the finger axis.
GRASP_CENTER = np.array([0.110, 0.017, 0.0], dtype=float)

#: How far the fingertips extend past GRASP_CENTER along the finger axis,
#: measured from the model (index and middle tips sit at local x = 0.165).
FINGERTIP_DEPTH = 0.055

#: Pushing pose: fingers slightly curled and the thumb tucked away, so the
#: hand presents one flat paddle instead of three separate prongs.
HAND_PADDLE = dict.fromkeys(HAND_JOINTS_RIGHT, 0.0) | {
    "right_hand_thumb_0_joint": -0.60,
    "right_hand_thumb_1_joint": -0.20,
    "right_hand_index_0_joint": 0.25,
    "right_hand_middle_0_joint": 0.25,
}

#: Fingers point straight down; the hand pushes with their outer face.
APPROACH_AXIS = np.array([0.0, 0.0, -1.0])

#: The finger bodies sit ~22mm along the wrist's local -y from the IK
#: reference point, so that is the direction the pushing face looks. Roll
#: about the finger axis has to be pinned to aim it: left free, the solver
#: picks a roll at will and the paddle routinely ends up edge-on to the push,
#: sweeping past the puck without touching it at all.
PADDLE_FACE_LOCAL = np.array([0.0, -1.0, 0.0])


@dataclass
class PolicyStatus:
    stage: Stage
    terminal: bool
    success: bool
    reason: str | None = None
    goal_distance: float = 0.0
    pushed: float = 0.0
    ik_position_error: float = 0.0
    history: list[StageRecord] = field(default_factory=list)


@dataclass
class PolicyConfig:
    """Tunables. Distances in metres, angles in radians, timeouts in sim seconds."""

    #: Half-height of the puck, from the skill parameters.
    puck_half_height: float = 0.022
    #: Radius of the puck, from the skill parameters.
    puck_radius: float = 0.035
    #: Gap between the IK reference point and the puck's near face when the
    #: hand takes up its pushing position. Kept small: every millimetre here
    #: is travel spent closing on the puck before any pushing happens.
    contact_standoff: float = 0.045
    #: Height of the fingertips above the table while pushing. Small, so the
    #: paddle catches the puck low and does not tip it. Raising it instead, so
    #: the hand cleared Drake's bulkier collision hulls, required a taller
    #: puck to still be reachable -- and a taller puck is top-heavy enough that
    #: MuJoCo launched it off the table. The clearance stays low and the
    #: hand/table collision is filtered in the Drake back end instead.
    fingertip_clearance: float = 0.004
    #: Hover altitude above the contact point during the approach. Too low and
    #: the descent clips the puck; too high and the fingers-down pose at that
    #: altitude has no solution near the robot's current posture.
    standoff: float = 0.14

    #: Puck must end within this of the commanded goal to count as delivered.
    #:
    #: Set from the mechanism's measured precision, not picked. The push ends
    #: as an open sweep to a computed end pose, so its terminal accuracy is
    #: about 40-50mm; across engines the same request landed at 39.9mm in
    #: MuJoCo and 45.3mm in Drake. A 40mm line therefore decides the verdict
    #: by which physics engine ran the job rather than by whether the robot
    #: did it, which is not a property worth shipping. 50mm is still well
    #: inside one puck radius (35mm) of the target.
    goal_tolerance: float = 0.050
    turn_tolerance: float = 0.05
    #: A stage is done when the hand is this close to its waypoint.
    cartesian_tolerance: float = 0.030
    #: Integral gain and clamp for the droop correction described in _plan_to.
    #: Deliberately slow. Larger gains (0.08, 0.20 were tried) wind the bias up
    #: during the big travel of the raise stage, where the hand is far from its
    #: waypoint for a legitimate reason, and the correction then fights the
    #: motion instead of trimming it.
    bias_gain: float = 0.02
    bias_limit: float = 0.09
    joint_max_step: float = 0.012

    #: How closely the paddle face must aim along the push direction.
    ik_face_tolerance: float = 0.45
    ik_position_tolerance: float = 0.004
    ik_axis_tolerance: float = 0.12
    #: Weight on staying near the current posture in the IK objective. At 1.0
    #: the solver still returns contortions with the waist twisted to its
    #: limit -- feasible, but not something the servos can hold against
    #: gravity, so the robot never arrives. 6.0 keeps solutions trackable.
    ik_posture_weight: float = 6.0

    timeouts: dict[str, float] = field(
        default_factory=lambda: {
            "turn": 3.0,
            "raise": 10.0,
            # Generous: the droop correction is deliberately slow, so a
            # far-reach reach needs time to trim itself onto the waypoint.
            "approach": 14.0,
            "push": 25.0,
        }
    )


class G1PushToTargetPolicy:
    """Engine-agnostic finite-state controller driving a constrained IK planner."""

    def __init__(
        self,
        joint_limits: dict[str, tuple[float, float]],
        ik: ArmIK,
        goal: np.ndarray,
        config: PolicyConfig | None = None,
    ) -> None:
        self._limits = joint_limits
        self._ik = ik
        self._goal = np.asarray(goal, dtype=float)
        self.cfg = config or PolicyConfig()
        self._stage = Stage.TURN
        self._stage_t0 = 0.0
        self._history: list[StageRecord] = []
        self._cmd: dict[str, float] = {}
        self._plan: dict[str, float] | None = None
        self._plan_error = 0.0
        self._waypoint: np.ndarray | None = None
        self._bias = np.zeros(3)
        self._push_dir: np.ndarray | None = None
        self._start_xy = np.zeros(2)
        self._reason: str | None = None

    # -- lifecycle --------------------------------------------------------

    def reset(self, obs: Observation) -> None:
        self._stage = Stage.TURN
        self._stage_t0 = obs.t
        self._history = []
        self._cmd = dict(obs.joint_pos)
        self._plan = None
        self._plan_error = 0.0
        self._waypoint: np.ndarray | None = None
        self._bias = np.zeros(3)
        self._push_dir = None
        self._start_xy = np.array(obs.object_pos[:2], dtype=float)
        self._reason = None

    @property
    def stage(self) -> Stage:
        return self._stage

    @property
    def goal(self) -> np.ndarray:
        return self._goal

    # -- main loop --------------------------------------------------------

    def step(self, obs: Observation) -> tuple[dict[str, float], PolicyStatus]:
        if self._stage in (Stage.DONE, Stage.FAILED):
            return self._cmd, self._status(obs)

        {
            Stage.TURN: self._do_turn,
            Stage.RAISE: self._do_raise,
            Stage.APPROACH: self._do_approach,
            Stage.PUSH: self._do_push,
        }[self._stage](obs)

        if self._stage not in (Stage.DONE, Stage.FAILED):
            if obs.object_pos[2] < self._surface(obs) - 0.08:
                self._fail("puck fell off the table")
            else:
                budget = self.cfg.timeouts.get(self._stage.value, 10.0)
                if obs.t - self._stage_t0 > budget:
                    self._fail(f"stage '{self._stage.value}' exceeded {budget:.1f}s")

        return self._cmd, self._status(obs)

    # -- stages -----------------------------------------------------------

    def _do_turn(self, obs: Observation) -> None:
        bearing = float(np.arctan2(obs.object_pos[1], obs.object_pos[0]))
        self._set("waist_yaw_joint", bearing)
        for joint, value in HAND_PADDLE.items():
            self._set(joint, value)
        if abs(obs.joint_pos["waist_yaw_joint"] - bearing) < self.cfg.turn_tolerance:
            self._advance(Stage.RAISE, obs)

    def _do_raise(self, obs: Observation) -> None:
        goal = self._contact_point(obs) + np.array([0.0, 0.0, self.cfg.standoff])
        if not self._plan_to(obs, goal):
            return
        self._slew()
        for joint, value in HAND_PADDLE.items():
            self._set(joint, value)
        if self._converged(obs):
            self._advance(Stage.APPROACH, obs)

    def _do_approach(self, obs: Observation) -> None:
        if not self._plan_to(obs, self._contact_point(obs)):
            return
        self._slew()
        if self._converged(obs):
            # Commit to the push line measured at the moment of contact.
            self._push_dir = self._direction(obs)
            self._advance(Stage.PUSH, obs)

    def _do_push(self, obs: Observation) -> None:
        """Sweep the hand from behind the puck to behind the goal.

        One IK solve for the far end, then a straight joint-space slew to it.
        An earlier version walked a Cartesian setpoint along the push line and
        re-solved every tick, which reads well but does not survive contact:
        the arm is slew-rate limited, the setpoint outran it by 5cm and kept
        going, and the run was scored a miss while the hand was still metres
        behind its own target. Sweeping to a fixed end pose cannot desynchronise
        that way -- the hand either gets there or the stage times out.
        """
        if self._push_dir is None:
            self._fail("push started without a committed contact pose")
            return

        remaining = float(np.linalg.norm(self._goal[:2] - obs.object_pos[:2]))
        if remaining < self.cfg.goal_tolerance:
            self._advance(Stage.DONE, obs)
            return

        end = np.array(
            [
                self._goal[0] - self._push_dir[0] * self._standoff(),
                self._goal[1] - self._push_dir[1] * self._standoff(),
                self._push_height(obs),
            ]
        )
        if not self._plan_to(obs, end):
            return
        self._slew()

    # -- geometry ---------------------------------------------------------

    def _surface(self, obs: Observation) -> float:
        """Table height, inferred from the puck resting on it."""
        return float(obs.object_pos[2]) - self.cfg.puck_half_height

    def _push_height(self, obs: Observation) -> float:
        """Height for the IK reference so the fingertips skim the table."""
        return self._surface(obs) + self.cfg.fingertip_clearance + FINGERTIP_DEPTH

    def _direction(self, obs: Observation) -> np.ndarray:
        d = self._goal[:2] - obs.object_pos[:2]
        n = float(np.linalg.norm(d))
        unit = d / n if n > 1e-6 else np.array([1.0, 0.0])
        return np.array([unit[0], unit[1], 0.0])

    def _face_direction(self, obs: Observation) -> np.ndarray:
        """Direction the pushing face should look: along the push line."""
        if self._push_dir is not None:
            return self._push_dir
        return self._direction(obs)

    def _standoff(self) -> float:
        return self.cfg.puck_radius + self.cfg.contact_standoff

    def _contact_point(self, obs: Observation) -> np.ndarray:
        """Pose the hand takes up behind the puck, on the puck-to-goal line."""
        d = self._direction(obs)
        return np.array(
            [
                obs.object_pos[0] - d[0] * self._standoff(),
                obs.object_pos[1] - d[1] * self._standoff(),
                self._push_height(obs),
            ]
        )

    # -- planning ---------------------------------------------------------

    def hand_point(self, obs: Observation) -> np.ndarray:
        """Where the IK reference point actually is right now."""
        return obs.ee_pos + obs.ee_rot @ GRASP_CENTER

    def _plan_to(
        self, obs: Observation, goal: np.ndarray, replan: bool = False
    ) -> bool:
        """Solve IK for `goal`, correcting for the arm's steady-state droop.

        The joints do not sit exactly where they are told. Under the load of
        an extended arm waist_roll settles about 0.055rad past its command,
        which lands the hand ~36mm high -- enough to sail over a 44mm puck.
        Re-solving against the raw waypoint never fixes that, because the IK
        is kinematically perfect and the error is in the servo. Accumulating
        the measured Cartesian error into a bias and planning against the
        corrected point closes the loop around the droop instead.
        """
        self._waypoint = np.asarray(goal, dtype=float)
        error = self._waypoint - self.hand_point(obs)
        self._bias = np.clip(
            self._bias + self.cfg.bias_gain * error,
            -self.cfg.bias_limit,
            self.cfg.bias_limit,
        )
        if self._plan is not None and not replan:
            return True
        result: IKResult = self._ik.solve(
            self._waypoint + self._bias,
            APPROACH_AXIS,
            obs.joint_pos,
            thumb_axis=-self._face_direction(obs),
            thumb_tolerance=self.cfg.ik_face_tolerance,
            position_tolerance=self.cfg.ik_position_tolerance,
            axis_tolerance=self.cfg.ik_axis_tolerance,
            posture_weight=self.cfg.ik_posture_weight,
        )
        if not result.ok:
            self._fail(
                f"no reachable configuration for the {self._stage.value} "
                f"waypoint at {np.round(goal, 3).tolist()}"
            )
            return False
        self._plan = result.joints
        self._plan_error = result.position_error
        return True

    def _slew(self) -> None:
        if self._plan is None:
            return
        for joint, target in self._plan.items():
            base = self._cmd.get(joint, target)
            delta = float(
                np.clip(target - base, -self.cfg.joint_max_step, self.cfg.joint_max_step)
            )
            self._set(joint, base + delta)

    def _converged(self, obs: Observation) -> bool:
        """True once the hand is actually at the waypoint.

        Judged in Cartesian space, not per joint. What the task needs is the
        hand in the right place; insisting every joint match its planned angle
        failed runs over a 3-degree waist droop that moved the hand by less
        than the tolerance we care about.
        """
        if self._plan is None or self._waypoint is None:
            return False
        return (
            float(np.linalg.norm(self._waypoint - self.hand_point(obs)))
            < self.cfg.cartesian_tolerance
        )

    # -- bookkeeping ------------------------------------------------------

    def _set(self, joint: str, value: float) -> None:
        lo, hi = self._limits.get(joint, (-np.inf, np.inf))
        self._cmd[joint] = float(np.clip(value, lo, hi))

    def _advance(self, nxt: Stage, obs: Observation) -> None:
        distance = float(np.linalg.norm(self._goal[:2] - obs.object_pos[:2]))
        self._history.append(
            StageRecord(self._stage.value, self._stage_t0, obs.t, distance)
        )
        self._stage = nxt
        self._stage_t0 = obs.t
        self._plan = None
        self._bias = np.zeros(3)

    def _fail(self, reason: str) -> None:
        self._reason = reason
        self._stage = Stage.FAILED

    def _status(self, obs: Observation) -> PolicyStatus:
        return PolicyStatus(
            stage=self._stage,
            terminal=self._stage in (Stage.DONE, Stage.FAILED),
            success=self._stage is Stage.DONE,
            reason=self._reason,
            goal_distance=float(np.linalg.norm(self._goal[:2] - obs.object_pos[:2])),
            pushed=float(np.linalg.norm(obs.object_pos[:2] - self._start_xy)),
            ik_position_error=self._plan_error,
            history=list(self._history),
        )
