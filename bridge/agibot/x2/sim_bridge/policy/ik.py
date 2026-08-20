"""Constrained inverse kinematics for the X2 left arm, solved with Drake.

Why a solver and not a Jacobian servo: a damped least-squares step has no
representation of a joint limit at all -- it bumps into one and then quietly
trades the task off against it, drifting instead of failing. Drake's
InverseKinematics states the problem properly: reach this point, point the
hand this way, respect every joint limit, and either return a configuration
satisfying all of it or report that none exists.

The plant here is kinematic only -- the base is welded to the world at the
height MuJoCo stands the robot at. It is used to *plan*; the resulting joint
targets are executed by whichever dynamics engine is running, which keeps the
sim-to-sim comparison about physics rather than about two different IK
implementations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pydrake.math import RigidTransform
from pydrake.multibody.inverse_kinematics import InverseKinematics
from pydrake.multibody.parsing import Parser
from pydrake.multibody.plant import MultibodyPlant
from pydrake.solvers import Solve

from ..simulation.base import ARM_JOINTS_LEFT, BASE_HEIGHT, END_EFFECTOR_BODY

#: Joints the solver may move. Only the left arm: letting it rearrange the
#: waist, legs or right arm would produce configurations the controller never
#: commands and the dynamics engine would not hold.
#:
#: The two wrist trim joints are excluded on top of that, because the arm
#: splits sharply into joints that can carry a load and joints that cannot:
#: shoulder and elbow are 36/36/24/24 Nm, while wrist pitch and roll are
#: 2.2 Nm each. A plan that spends those is not executable. Asked to roll the
#: hand 2.3 rad, wrist roll saturated at 2.2 Nm and stalled against the hip
#: with an equal and opposite constraint force, leaving the tool 37cm from a
#: waypoint the solver called reachable -- the planner was solving in a
#: kinematic world the servo could not enter.
#:
#: That leaves five joints for a five-constraint problem (position plus tool
#: axis), so the solve is exactly determined and the posture cost has no
#: redundancy left to spend. It is still solvable everywhere the task needs;
#: `tools/workspace.py` measures that rather than assuming it.
_WEAK_WRIST_JOINTS = ("left_wrist_pitch_joint", "left_wrist_roll_joint")
FREE_JOINTS: tuple[str, ...] = tuple(
    j for j in ARM_JOINTS_LEFT if j not in _WEAK_WRIST_JOINTS
)

#: Working point in the end-effector frame: near the bottom tip of the hand.
#:
#: Measured from both descriptions rather than assumed. The hand is a slab
#: roughly 20cm deep hanging *below* the `left_wrist_roll_link` frame --
#: MuJoCo's collision mesh spans z from -0.182 to +0.016 in that frame,
#: Drake's simplified box from -0.170 to -0.030 -- so the frame origin is not
#: the contact surface but the top lip. Planning to the origin aimed the hand
#: 10cm below every waypoint: MuJoCo still caught the puck with the top edge
#: of its larger mesh and looked like it worked, while Drake's smaller box
#: passed underneath and never touched it at all.
#:
#: -0.165 is chosen to sit inside both hulls while leaving the hand's lowest
#: geometry just clear of the table: driven to 25mm above the surface, the
#: MuJoCo mesh bottoms out 8mm above it and the Drake box 20mm, and both still
#: overlap a 44mm puck. Aiming any lower buries the slab in the table; any
#: higher and it rides over the puck.
TOOL_OFFSET = np.array([0.0, 0.0, -0.165], dtype=float)

#: Direction the hand presents while pushing, in the end-effector frame, and
#: the world direction it is asked to align with.
#:
#: The hand slab extends along local -z and the link's local +z points very
#: nearly straight up at rest, so asking +z to stay up is asking the hand to
#: keep hanging down -- what the arm does naturally, and what keeps the slab's
#: face against the puck rather than tilted into the table.
#:
#: Rotation about the vertical is deliberately left free. It decides only
#: which face of the slab meets the puck, and both are flat and wider than the
#: puck, so pinning it buys nothing and costs a degree of freedom. That matters
#: here: with only five load-bearing joints (see FREE_JOINTS) a constraint that
#: fixes all three angles leaves the solve overdetermined in practice. An
#: earlier version pinned local x to world x, which is not a property the task
#: needs, and the reachable workspace collapsed to 15 of 90 sampled points at
#: hover height.
TOOL_AXIS = np.array([0.0, 0.0, 1.0], dtype=float)
TOOL_AXIS_WORLD = np.array([0.0, 0.0, 1.0], dtype=float)


def default_description_root() -> Path:
    """Root of the AgiBot X2 checkout, honouring an override."""
    override = os.environ.get("X2_DESCRIPTION_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "x2" / "X2_URDF-v1.3.0"


def default_urdf() -> Path:
    override = os.environ.get("X2_URDF")
    if override:
        return Path(override).expanduser()
    return default_description_root() / "x2_ultra.urdf"


@dataclass
class IKResult:
    """Outcome of one solve."""

    ok: bool
    joints: dict[str, float]
    position_error: float
    axis_error: float
    detail: str = ""


class ArmIK:
    """Solves left-arm configurations for a desired hand pose."""

    def __init__(self, urdf: Path | None = None) -> None:
        path = Path(urdf) if urdf is not None else default_urdf()
        if not path.is_file():
            raise FileNotFoundError(
                f"X2 URDF not found at {path}. Clone "
                "AgibotTech/agibot_x2_urdf, or set X2_URDF."
            )
        self._plant = MultibodyPlant(time_step=0.0)
        parser = Parser(self._plant)
        parser.package_map().PopulateFromFolder(str(path.parent))
        parser.AddModels(str(path))
        self._plant.WeldFrames(
            self._plant.world_frame(),
            self._plant.GetFrameByName("base_link"),
            RigidTransform([0.0, 0.0, BASE_HEIGHT]),
        )
        self._plant.Finalize()
        self._context = self._plant.CreateDefaultContext()
        self._ee = self._plant.GetFrameByName(END_EFFECTOR_BODY)
        self._joints = {
            self._plant.get_joint(j).name(): self._plant.get_joint(j)
            for j in self._plant.GetJointIndices()
            if self._plant.get_joint(j).num_positions() == 1
        }
        missing = [n for n in FREE_JOINTS if n not in self._joints]
        if missing:
            raise KeyError(f"URDF is missing expected joints: {missing}")

    @property
    def joint_names(self) -> tuple[str, ...]:
        return FREE_JOINTS

    def solve(
        self,
        target: np.ndarray,
        seed: dict[str, float],
        axis: np.ndarray | None = None,
        position_tolerance: float = 0.008,
        axis_tolerance: float = 0.50,
        posture_weight: float = 1.0,
        restarts: int = 5,
    ) -> IKResult:
        """Solve for arm joints placing the tool point at `target`.

        `seed` supplies the current pose of every joint. Joints outside
        FREE_JOINTS are pinned to their seed values, so the solve cannot
        quietly rearrange the rest of the robot into a pose the controller
        never commands.

        The solve is retried from randomised arm configurations because a
        single attempt from the rest pose lands in a local minimum and reports
        infeasible on targets that are provably reachable -- generated, in
        testing, by sampling joint angles and reading off the resulting tool
        position. Five restarts took 12/12 of those; one took 0/12 at the
        posture weight this planner originally used. The mean cost is 1.2
        attempts, because the first guess usually works.
        """
        target = np.asarray(target, dtype=float)
        axis = TOOL_AXIS if axis is None else np.asarray(axis, dtype=float)

        q0 = self._seed_vector(seed)
        rng = np.random.default_rng(0)
        last_detail = "no configuration satisfies the constraints"
        for attempt in range(max(1, restarts)):
            guess = q0.copy()
            if attempt > 0:
                for name in FREE_JOINTS:
                    joint = self._joints[name]
                    lo = joint.position_lower_limits()[0]
                    hi = joint.position_upper_limits()[0]
                    guess[joint.position_start()] = float(rng.uniform(lo, hi))
            found = self._attempt(
                target, axis, q0, guess,
                position_tolerance, axis_tolerance, posture_weight,
            )
            if found is not None:
                return found
        return IKResult(False, {}, float("inf"), float("inf"), last_detail)

    def _attempt(
        self,
        target: np.ndarray,
        axis: np.ndarray,
        q0: np.ndarray,
        guess: np.ndarray,
        position_tolerance: float,
        axis_tolerance: float,
        posture_weight: float,
    ) -> "IKResult | None":
        ik = InverseKinematics(self._plant, self._context)
        prog = ik.prog()
        q = ik.q()

        ik.AddPositionConstraint(
            self._ee,
            TOOL_OFFSET,
            self._plant.world_frame(),
            target - position_tolerance,
            target + position_tolerance,
        )
        ik.AddAngleBetweenVectorsConstraint(
            self._ee,
            axis,
            self._plant.world_frame(),
            TOOL_AXIS_WORLD,
            0.0,
            min(axis_tolerance, np.pi),
        )

        free = set(FREE_JOINTS)
        for name, joint in self._joints.items():
            if name in free:
                continue
            idx = joint.position_start()
            prog.AddBoundingBoxConstraint(q0[idx], q0[idx], q[idx])

        # Prefer configurations near where the arm already is, so consecutive
        # waypoints stay close and the arm does not reconfigure mid-motion.
        # The weight is deliberately mild: at 6.0 it pinned the solver to the
        # rest pose hard enough that reachable targets came back infeasible.
        if posture_weight > 0.0:
            prog.AddQuadraticErrorCost(posture_weight * np.eye(len(q0)), guess, q)
        prog.SetInitialGuess(q, guess)

        result = Solve(prog)
        if not result.is_success():
            return None

        qs = result.GetSolution(q)
        joints = {
            name: float(qs[self._joints[name].position_start()])
            for name in FREE_JOINTS
        }
        pos_err, axis_err = self._evaluate(qs, target, axis)
        return IKResult(True, joints, pos_err, axis_err)

    # -- unused placeholder removed --

    def tool_point(self, seed: dict[str, float]) -> np.ndarray:
        """Where the tool point sits for a given joint configuration."""
        self._plant.SetPositions(self._context, self._seed_vector(seed))
        X = self._plant.CalcRelativeTransform(
            self._context, self._plant.world_frame(), self._ee
        )
        return np.array(X @ TOOL_OFFSET, dtype=float)

    # -- internals --------------------------------------------------------

    def _seed_vector(self, seed: dict[str, float]) -> np.ndarray:
        q0 = self._plant.GetPositions(self._context).copy()
        for name, value in seed.items():
            joint = self._joints.get(name)
            if joint is None:
                continue
            lo = joint.position_lower_limits()[0]
            hi = joint.position_upper_limits()[0]
            q0[joint.position_start()] = float(np.clip(value, lo, hi))
        return q0

    def _evaluate(
        self, q: np.ndarray, target: np.ndarray, axis: np.ndarray
    ) -> tuple[float, float]:
        self._plant.SetPositions(self._context, q)
        X = self._plant.CalcRelativeTransform(
            self._context, self._plant.world_frame(), self._ee
        )
        tool = X @ TOOL_OFFSET
        got = X.rotation().matrix() @ axis
        got = got / max(float(np.linalg.norm(got)), 1e-9)
        want = TOOL_AXIS_WORLD
        angle = float(np.arccos(float(np.clip(np.dot(got, want), -1.0, 1.0))))
        return float(np.linalg.norm(tool - target)), angle
