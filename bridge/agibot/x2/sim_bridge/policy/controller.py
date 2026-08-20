"""Task policy for the AgiBot X2 push-to-target skill.

This is deliberately *not* a recorded trajectory. Every joint command is
derived at run time from the current observation:

  * the push direction comes from the live puck-to-goal vector,
  * each waypoint is turned into a joint configuration by a constrained IK
    solve (see `ik.py`) against the puck's *measured* pose, and
  * stage transitions fire on sensed conditions -- tool proximity, achieved
    puck displacement -- never on a timer.

Timers exist only as failure guards. Both ends of the motion are parameters of
the paid action: the payer picks where the puck starts *and* where it must end
up. A replayed animation has nothing to replay, which is the property the
bounty's "cannot simply replay a predefined animation" rule is after.

The X2 wrist terminates in a single solid link with no fingers, so the flat
side of that link is the pusher. There is nothing to open or close, which
removes the failure mode that dominated the two robots tried before this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..simulation.base import Observation
from .ik import ArmIK, IKResult
from .stages import Stage, StageRecord


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

    #: Radius of the puck, from the skill parameters.
    puck_radius: float = 0.035
    #: Half-height of the puck, from the skill parameters.
    puck_half_height: float = 0.022
    #: Gap between the tool point and the puck's near face when the arm takes
    #: up its pushing position. Kept small: every millimetre is travel spent
    #: closing on the puck before any pushing happens.
    contact_standoff: float = 0.045
    #: Height of the tool point above the work surface while pushing.
    #:
    #: Set from the hand geometry both engines agree on (see `ik.TOOL_OFFSET`).
    #: The tool point sits near the hand's lower tip, so this is very nearly
    #: the clearance between the hand and the table: 25mm leaves the MuJoCo
    #: mesh 8mm clear and the Drake box 20mm, while both still overlap the
    #: side of a 44mm puck. Below about 15mm the hand catches the table in
    #: MuJoCo; above about 40mm it rides over the puck in Drake.
    push_height: float = 0.025
    #: Hover altitude above the surface for the travel leg.
    #:
    #: Only high enough to carry the hand over a 44mm puck: the tool point
    #: rides 75mm up, putting the lowest MuJoCo hand geometry 14mm above the
    #: puck's top face. Higher is not free -- the tool point sits 165mm below
    #: the wrist, so every centimetre of hover lifts the wrist a centimetre
    #: nearer the shoulder, and reachable hover targets fall away steadily
    #: above this: 57 of 90 sampled points at 0.895m, 41 at 0.925m, 31 at
    #: 0.940m.
    hover_height: float = 0.075

    #: Puck must end within this of the commanded goal to count as delivered.
    goal_tolerance: float = 0.050
    #: A stage is done when the tool is this close to its waypoint.
    cartesian_tolerance: float = 0.055
    #: Per-tick joint slew toward the planned configuration.
    joint_max_step: float = 0.012

    #: How far past the goal the stroke aims, measured from the goal itself.
    push_overrun: float = 0.060
    #: How far the puck may drift off the committed push line before the
    #: policy backs off and re-approaches rather than sweeping past it.
    reacquire_lateral: float = 0.045
    #: Cap on those retries, so a puck that cannot be delivered fails cleanly
    #: instead of looping.
    max_reacquires: int = 3

    #: Integral gain and clamp correcting the arm's steady-state droop.
    bias_gain: float = 0.05
    bias_limit: float = 0.15
    #: Error below which the bias is allowed to accumulate.
    #:
    #: Droop is a steady-state effect, so the correction is only meaningful
    #: once the arm has arrived. Integrating during the transit instead reads
    #: the whole remaining distance as droop: on the raise leg that is over
    #: 350mm, the bias saturates on every axis within a few ticks, and the
    #: solver is handed a target 150mm past the one the stage asked for.
    bias_engage: float = 0.08

    ik_position_tolerance: float = 0.008
    ik_axis_tolerance: float = 0.50
    ik_posture_weight: float = 1.0

    #: Failure guards, not pacing. Sized from measured stage durations plus
    #: headroom: the raise leg travels from the arm's hanging rest pose up to
    #: table height and took 3.5-12s across the sampled workspace, so a 12s
    #: budget was cutting off runs that were still converging.
    timeouts: dict[str, float] = field(
        default_factory=lambda: {
            "raise": 25.0,
            "traverse": 15.0,
            "descend": 15.0,
            "push": 35.0,
        }
    )


class X2PushPolicy:
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
        self._stage = Stage.RAISE
        self._stage_t0 = 0.0
        self._history: list[StageRecord] = []
        self._cmd: dict[str, float] = {}
        self._plan: dict[str, float] | None = None
        self._plan_error = 0.0
        self._waypoint: np.ndarray | None = None
        self._bias = np.zeros(3)
        self._push_dir: np.ndarray | None = None
        self._start_xy = np.zeros(2)
        self._surface_z = 0.0
        self._reacquires = 0
        self._reason: str | None = None

    # -- lifecycle --------------------------------------------------------

    def reset(self, obs: Observation) -> None:
        self._stage = Stage.RAISE
        self._stage_t0 = obs.t
        self._history = []
        self._cmd = dict(obs.joint_pos)
        self._plan = None
        self._plan_error = 0.0
        self._waypoint = None
        self._bias = np.zeros(3)
        self._push_dir = None
        self._start_xy = np.array(obs.object_pos[:2], dtype=float)
        self._surface_z = float(obs.object_pos[2]) - self.cfg.puck_half_height
        self._reacquires = 0
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

        # Delivery is checked every tick, not only at the top of the push.
        # Checking it only there meant a puck that arrived during a
        # re-acquisition was scored a failure while sitting 46mm from the
        # goal, inside a 50mm tolerance -- the task was done and the policy
        # did not notice.
        if self._stage is not Stage.RAISE:
            if float(np.linalg.norm(self._goal[:2] - obs.object_pos[:2])) < (
                self.cfg.goal_tolerance
            ):
                self._advance(Stage.DONE, obs)
                return self._cmd, self._status(obs)

        {
            Stage.RAISE: self._do_raise,
            Stage.TRAVERSE: self._do_traverse,
            Stage.DESCEND: self._do_descend,
            Stage.PUSH: self._do_push,
        }[self._stage](obs)

        if self._stage not in (Stage.DONE, Stage.FAILED):
            if obs.object_pos[2] < self._surface_z - 0.08:
                self._fail("puck fell off the work surface")
            else:
                budget = self.cfg.timeouts.get(self._stage.value, 10.0)
                if obs.t - self._stage_t0 > budget:
                    self._fail(f"stage '{self._stage.value}' exceeded {budget:.1f}s")

        return self._cmd, self._status(obs)

    # -- stages -----------------------------------------------------------

    def _do_raise(self, obs: Observation) -> None:
        """Lift to hover altitude directly above the contact point."""
        contact = self._contact_point(obs)
        hover = np.array([contact[0], contact[1], self._hover_z()])
        if not self._plan_to(obs, hover, soft=True):
            if not self._plan_to(obs, contact):
                return
        self._slew()
        if self._converged(obs):
            self._advance(Stage.TRAVERSE, obs)

    def _do_traverse(self, obs: Observation) -> None:
        """Settle precisely over the contact point before descending.

        Hover altitude is the preferred approach but not a requirement. The
        tool point sits 165mm below the wrist, so hovering costs reach: at
        hover the solver covers a band roughly 60mm wide in x, against most of
        the table at push height. A contact point outside that band is common
        on a re-approach after the puck has drifted, and it was the single
        biggest source of failed runs -- the arm was already low and behind
        the puck, with a clear path to a reachable waypoint, and gave up.
        The contact point lies between the robot and the puck by construction,
        so closing on it at push height crosses nothing.
        """
        contact = self._contact_point(obs)
        hover = np.array([contact[0], contact[1], self._hover_z()])
        if not self._plan_to(obs, hover, replan=True, soft=True):
            if not self._plan_to(obs, contact, replan=True):
                return
        self._slew()
        if self._converged(obs):
            self._advance(Stage.DESCEND, obs)

    def _do_descend(self, obs: Observation) -> None:
        if not self._plan_to(obs, self._contact_point(obs)):
            return
        self._slew()
        if self._converged(obs):
            # Commit to the push line measured at the moment of contact.
            self._push_dir = self._direction(obs)
            self._advance(Stage.PUSH, obs)

    def _do_push(self, obs: Observation) -> None:
        """Sweep the tool from behind the puck to behind the goal.

        One IK solve for the far end, then a straight joint-space slew to it.
        Walking a Cartesian setpoint along the line and re-solving each tick
        reads better but does not survive contact: the arm is slew-rate
        limited, the setpoint outruns it, and the run is scored a miss while
        the tool is still far behind its own target.
        """
        if self._push_dir is None:
            self._fail("push started without a committed contact pose")
            return

        remaining = float(np.linalg.norm(self._goal[:2] - obs.object_pos[:2]))
        if remaining < self.cfg.goal_tolerance:
            self._advance(Stage.DONE, obs)
            return

        # Re-acquire if the puck has slid off the line committed at contact.
        # The sweep is planned once and executed open-loop, which is what
        # keeps it robust against slew-rate limits -- but a puck that drifts
        # sideways then gets passed by, and the arm finishes its stroke with
        # the puck still short of the goal and no way to recover.
        tool = self.tool_point(obs)
        behind = float(np.dot(obs.object_pos[:2] - tool[:2], self._push_dir[:2]))
        lateral = float(
            np.linalg.norm(
                (obs.object_pos[:2] - tool[:2])
                - behind * self._push_dir[:2]
            )
        )
        if behind < 0.0 or lateral > self.cfg.reacquire_lateral:
            self._push_dir = None
            self._reacquires += 1
            if self._reacquires > self.cfg.max_reacquires:
                self._fail(
                    f"puck escaped the push line {self._reacquires} times; "
                    f"still {remaining:.3f}m from the goal"
                )
                return
            # Resume from the travel leg, not the raise: the arm is already
            # at working height, and re-running the full climb both wastes
            # its budget and is what made the first re-acquire time out.
            self._advance(Stage.TRAVERSE, obs)
            return

        # Aim the stroke past the goal by the standoff plus a margin. Ending
        # it exactly one standoff short leaves the puck wherever friction
        # stopped it, which measured 50-65mm from the goal on five of the
        # sampled targets -- just outside the 50mm tolerance. Overrunning
        # costs nothing when the puck arrives early, because the stage exits
        # on the puck's measured position, not on the arm finishing its path.
        reach = self.cfg.push_overrun - self._standoff()
        end = np.array([
            self._goal[0] + self._push_dir[0] * reach,
            self._goal[1] + self._push_dir[1] * reach,
            self._surface_z + self.cfg.push_height,
        ])
        if not self._plan_to(obs, end):
            return
        self._slew()

    # -- geometry ---------------------------------------------------------

    def _hover_z(self) -> float:
        return self._surface_z + self.cfg.hover_height

    def _direction(self, obs: Observation) -> np.ndarray:
        d = self._goal[:2] - obs.object_pos[:2]
        n = float(np.linalg.norm(d))
        unit = d / n if n > 1e-6 else np.array([1.0, 0.0])
        return np.array([unit[0], unit[1], 0.0])

    def _standoff(self) -> float:
        return self.cfg.puck_radius + self.cfg.contact_standoff

    def _contact_point(self, obs: Observation) -> np.ndarray:
        """Pose the tool takes up behind the puck, on the puck-to-goal line."""
        d = self._push_dir if self._push_dir is not None else self._direction(obs)
        return np.array([
            obs.object_pos[0] - d[0] * self._standoff(),
            obs.object_pos[1] - d[1] * self._standoff(),
            self._surface_z + self.cfg.push_height,
        ])

    # -- planning ---------------------------------------------------------

    def tool_point(self, obs: Observation) -> np.ndarray:
        return self._ik.tool_point(obs.joint_pos)

    def _plan_to(
        self,
        obs: Observation,
        goal: np.ndarray,
        replan: bool = False,
        soft: bool = False,
    ) -> bool:
        """Solve IK for `goal`, correcting for the arm's steady-state droop.

        The joints do not sit exactly where they are told: under the load of an
        extended arm they settle short, which offsets the tool. Re-solving
        against the raw waypoint never fixes that, because the IK is
        kinematically exact and the error is in the servo. Accumulating the
        measured Cartesian error into a bias closes the loop around the droop.
        """
        self._waypoint = np.asarray(goal, dtype=float)
        error = self._waypoint - self.tool_point(obs)
        if float(np.linalg.norm(error)) < self.cfg.bias_engage:
            self._bias = np.clip(
                self._bias + self.cfg.bias_gain * error,
                -self.cfg.bias_limit,
                self.cfg.bias_limit,
            )
        if self._plan is not None and not replan:
            return True
        def attempt(target: np.ndarray) -> IKResult:
            return self._ik.solve(
                target,
                obs.joint_pos,
                position_tolerance=self.cfg.ik_position_tolerance,
                axis_tolerance=self.cfg.ik_axis_tolerance,
                posture_weight=self.cfg.ik_posture_weight,
            )

        result: IKResult = attempt(self._waypoint + self._bias)
        if not result.ok:
            # The bias is a correction for servo droop, not part of the task.
            # Near the edge of the workspace it can carry an otherwise
            # reachable waypoint outside it -- measured at 27mm of bias on a
            # re-approach whose raw target solved perfectly well -- and
            # failing the run over the correction rather than the goal is the
            # wrong trade. Drop it and take the honest waypoint instead.
            result = attempt(self._waypoint)
            if result.ok:
                self._bias = np.zeros(3)
        if not result.ok:
            if soft:
                return False
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
        """True once the tool is actually at the waypoint.

        Judged in Cartesian space, not per joint: what the task needs is the
        tool in the right place, and insisting every joint match its planned
        angle fails runs over a droop that moves the tool less than the
        tolerance that matters.
        """
        if self._plan is None or self._waypoint is None:
            return False
        return (
            float(np.linalg.norm(self._waypoint - self.tool_point(obs)))
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
