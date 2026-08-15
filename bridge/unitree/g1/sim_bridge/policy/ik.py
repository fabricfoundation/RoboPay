"""Constrained inverse kinematics for the G1 right arm, solved with Drake.

Why a solver and not a Jacobian servo. The first version of this controller
stepped the arm with damped least squares on the live Jacobian. That works
until the solution needs a joint that is already at its limit, and then it
does not fail -- it quietly trades the task off against the limit and drifts.
In practice right_shoulder_roll and right_wrist_yaw saturated, the hand ended
up 40cm outboard of the block with its fingers pointing sideways, and no
amount of gain tuning fixed it because the servo has no representation of a
joint limit at all. It only ever bumps into one.

Drake's InverseKinematics states the problem properly: reach this point, point
the fingers this way, respect every joint limit, and either return a
configuration that satisfies all of it or report that none exists. The planner
calls this once per waypoint and the controller interpolates toward the answer,
so limits are handled where they are actually known rather than discovered by
collision.

The plant here is kinematic only -- the pelvis is welded to the world at the
standing height. It is used to *plan*; the resulting joint targets are then
executed by whichever dynamics engine is running, which is what keeps the
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

from ..simulation.base import ARM_JOINTS_RIGHT, END_EFFECTOR_BODY, WAIST_JOINTS

#: Joints the solver may move. The waist belongs here: with it pinned, the
#: arm alone cannot bring the hand up to hover altitude over a block at
#: comfortable reach, and the solve reports infeasible. Reaching high is a
#: whole-torso motion on this robot, not an arm-only one.
FREE_JOINTS: tuple[str, ...] = ARM_JOINTS_RIGHT + WAIST_JOINTS

#: Height of the pelvis above the floor in the model's standing pose. Matches
#: the menagerie 'stand' keyframe so planned configurations line up with what
#: the dynamics engines actually hold.
PELVIS_HEIGHT = 0.793


def default_urdf() -> Path:
    """Locate the OBJ-converted G1 description, honouring an override."""
    override = os.environ.get("G1_URDF")
    if override:
        return Path(override).expanduser()
    return (
        Path(__file__).resolve().parents[5]
        / "assets"
        / "g1_description_obj"
        / "g1_29dof_with_hand.urdf"
    )


@dataclass
class IKResult:
    """Outcome of one solve."""

    ok: bool
    joints: dict[str, float]
    position_error: float
    axis_error: float
    detail: str = ""


class ArmIK:
    """Solves right-arm configurations for a desired hand pose."""

    def __init__(
        self,
        urdf: Path | None = None,
        grasp_center: np.ndarray | None = None,
    ) -> None:
        path = Path(urdf) if urdf is not None else default_urdf()
        if not path.is_file():
            raise FileNotFoundError(
                f"G1 URDF not found at {path}. Run "
                "bridge/unitree/g1/sim_bridge/tools/convert_meshes.py first, "
                "or set G1_URDF."
            )
        self._plant = MultibodyPlant(time_step=0.0)
        parser = Parser(self._plant)
        parser.package_map().PopulateFromFolder(str(path.parent))
        parser.AddModels(str(path))
        self._plant.WeldFrames(
            self._plant.world_frame(),
            self._plant.GetFrameByName("pelvis"),
            RigidTransform([0.0, 0.0, PELVIS_HEIGHT]),
        )
        self._plant.Finalize()
        self._context = self._plant.CreateDefaultContext()
        self._wrist = self._plant.GetFrameByName(END_EFFECTOR_BODY)
        self._grasp_center = (
            np.array([0.110, 0.017, 0.0]) if grasp_center is None
            else np.asarray(grasp_center, dtype=float)
        )
        self._joint_index = {
            self._plant.get_joint(j).name(): self._plant.get_joint(j)
            for j in self._plant.GetJointIndices()
        }

    @property
    def joint_names(self) -> tuple[str, ...]:
        return FREE_JOINTS

    def solve(
        self,
        target: np.ndarray,
        axis: np.ndarray,
        seed: dict[str, float],
        position_tolerance: float = 0.008,
        axis_tolerance: float = 0.15,
        thumb_axis: np.ndarray | None = None,
        thumb_tolerance: float = 0.45,
        posture_weight: float = 1.0,
    ) -> IKResult:
        """Solve for arm joints placing the grasp point at `target`.

        `seed` supplies the current pose of every joint. Joints outside
        FREE_JOINTS are pinned to their seed values so the solve cannot quietly
        rearrange the legs to reach an otherwise impossible pose -- the
        dynamics engine would refuse to follow that anyway.

        `thumb_axis` pins the roll about the fingers by asking the wrist's
        local +y (the thumb side) to point a given way in the world. Leaving
        roll free is fine for a single pose, but consecutive waypoints then get
        unrelated roll solutions, and slewing between two of them twists a held
        object straight out of the hand -- which is exactly how the lift stage
        used to lose the block half a second after picking it up.
        """
        target = np.asarray(target, dtype=float)
        axis = np.asarray(axis, dtype=float)

        q0 = self._seed_vector(seed)
        ik = InverseKinematics(self._plant, self._context)
        prog = ik.prog()
        q = ik.q()

        ik.AddPositionConstraint(
            self._wrist,
            self._grasp_center,
            self._plant.world_frame(),
            target - position_tolerance,
            target + position_tolerance,
        )
        ik.AddAngleBetweenVectorsConstraint(
            self._wrist,
            np.array([1.0, 0.0, 0.0]),
            self._plant.world_frame(),
            axis,
            0.0,
            axis_tolerance,
        )
        if thumb_axis is not None:
            ik.AddAngleBetweenVectorsConstraint(
                self._wrist,
                np.array([0.0, 1.0, 0.0]),
                self._plant.world_frame(),
                np.asarray(thumb_axis, dtype=float),
                0.0,
                thumb_tolerance,
            )

        free = set(FREE_JOINTS)
        for name, joint in self._joint_index.items():
            if joint.num_positions() != 1 or name in free:
                continue
            idx = joint.position_start()
            prog.AddBoundingBoxConstraint(q0[idx], q0[idx], q[idx])

        # Prefer the nearest configuration to where the robot already is.
        #
        # Without this the solve is pure feasibility, and "feasible" includes
        # contortions: one hover pose came back with shoulder_yaw and
        # waist_roll pinned to their limits and the waist twisted 1.7rad, a
        # posture the servos cannot hold against gravity, so the robot simply
        # never arrived. A quadratic cost on deviation from the seed keeps the
        # answer natural and trackable, and also keeps consecutive waypoints
        # close together so the arm does not reconfigure between them.
        if posture_weight > 0.0:
            prog.AddQuadraticErrorCost(
                posture_weight * np.eye(len(q0)), q0, q
            )

        prog.SetInitialGuess(q, q0)
        result = Solve(prog)
        if not result.is_success():
            return IKResult(False, {}, float("inf"), float("inf"),
                            "no configuration satisfies the constraints")

        qs = result.GetSolution(q)
        joints = {}
        for name in FREE_JOINTS:
            joints[name] = float(qs[self._joint_index[name].position_start()])

        pos_err, axis_err = self._evaluate(qs, target, axis)
        return IKResult(True, joints, pos_err, axis_err)

    # -- internals --------------------------------------------------------

    def _seed_vector(self, seed: dict[str, float]) -> np.ndarray:
        q0 = self._plant.GetPositions(self._context).copy()
        for name, value in seed.items():
            joint = self._joint_index.get(name)
            if joint is None or joint.num_positions() != 1:
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
            self._context, self._plant.world_frame(), self._wrist
        )
        grasp = X @ self._grasp_center
        got = X.rotation().matrix() @ np.array([1.0, 0.0, 0.0])
        got = got / max(float(np.linalg.norm(got)), 1e-9)
        want = axis / max(float(np.linalg.norm(axis)), 1e-9)
        angle = float(np.arccos(float(np.clip(np.dot(got, want), -1.0, 1.0))))
        return float(np.linalg.norm(grasp - target)), angle
