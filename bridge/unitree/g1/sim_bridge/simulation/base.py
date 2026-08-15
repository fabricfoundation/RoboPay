"""Engine-agnostic contract shared by the MuJoCo and Drake back ends.

Sim-to-sim validation is only meaningful if both engines are driven by the
*same* policy object. That requires the policy to never touch an engine API
directly: it consumes an `Observation` and returns joint targets keyed by
joint name. Joint names are the natural key here because the menagerie MJCF
and the official Unitree URDF agree on all 43 of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# The 43 actuated joints, in the order both back ends report them. Kept
# explicit rather than derived so a model change shows up as a loud mismatch
# instead of a silently reordered vector.
ARM_JOINTS_RIGHT = (
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)

HAND_JOINTS_RIGHT = (
    "right_hand_thumb_0_joint",
    "right_hand_thumb_1_joint",
    "right_hand_thumb_2_joint",
    "right_hand_index_0_joint",
    "right_hand_index_1_joint",
    "right_hand_middle_0_joint",
    "right_hand_middle_1_joint",
)

WAIST_JOINTS = (
    "waist_yaw_joint",
    "waist_roll_joint",
    "waist_pitch_joint",
)

# Body whose frame the policy treats as the end effector.
END_EFFECTOR_BODY = "right_wrist_yaw_link"


@dataclass(frozen=True)
class Observation:
    """One engine-independent snapshot of the world."""

    t: float
    """Simulated seconds since reset."""

    joint_pos: dict[str, float]
    """Position of every actuated joint, keyed by name."""

    ee_pos: np.ndarray
    """End-effector position in world coordinates, shape (3,)."""

    ee_rot: np.ndarray
    """End-effector rotation matrix, shape (3, 3)."""

    object_pos: np.ndarray
    """Graspable block position in world coordinates, shape (3,)."""


    hand_contacts: int
    """Number of distinct contacts between right-hand geometry and the block."""

    grasp_force: float
    """Total normal force across those contacts, in newtons."""

    self_collision: bool
    """True if a non-hand body is interpenetrating the block or the pedestal."""

    extras: dict[str, float] = field(default_factory=dict)
    """Back-end specific diagnostics; never read by the policy."""


class SimEnv:
    """Interface both back ends implement.

    Implementations must be deterministic given the same seed and target, so
    that a sim-to-sim disagreement is attributable to engine physics rather
    than to nondeterminism in the harness.
    """

    #: Control period in seconds. Both back ends must agree on this so the
    #: policy sees the same decision rate regardless of engine.
    control_dt: float = 0.01

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        """Position limit of every actuated joint, keyed by name.

        The policy clamps its own commands rather than relying on the engine
        to saturate them, so that MuJoCo and Drake receive byte-identical
        targets and any divergence is purely dynamical.
        """
        raise NotImplementedError

    def reset(self) -> Observation:
        """Return the world to its start state and report the first observation."""
        raise NotImplementedError

    def step(self, targets: dict[str, float]) -> Observation:
        """Hold `targets` for one control period and report the result."""
        raise NotImplementedError

    def ee_jacobian(self) -> np.ndarray:
        """Full spatial Jacobian of the end effector w.r.t. the right-arm joints.

        Shape (6, len(ARM_JOINTS_RIGHT)): rows 0-2 are translational, rows 3-5
        rotational, both in world coordinates. The arm has 7 joints, so a
        6-DOF task leaves one redundant degree of freedom.

        Orientation matters here and is not a refinement: the payer chooses
        where the block spawns, so the hand has to arrive in a graspable pose
        for an arbitrary target rather than whichever pose the position-only
        solution happens to drift into.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release engine resources. Safe to call more than once."""

    def __enter__(self) -> "SimEnv":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
