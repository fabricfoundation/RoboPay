"""Engine-agnostic contract shared by the MuJoCo and Drake back ends.

Sim-to-sim validation is only meaningful if both engines are driven by the
*same* policy object. That requires the policy to never touch an engine API
directly: it consumes an `Observation` and returns joint targets keyed by
joint name.

AgiBot names its MuJoCo actuators `motor_<joint>` while the URDF calls the
joint `<joint>`. The policy speaks URDF names throughout -- they are what the
IK planner solves against -- and the MuJoCo back end translates at its own
boundary. One vocabulary, translated in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: The left arm, shoulder to wrist. Seven joints, so a 6-DOF pose task leaves
#: one redundant degree of freedom for the posture cost to spend.
ARM_JOINTS_LEFT = (
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_joint",
    "left_wrist_yaw_joint",
    "left_wrist_pitch_joint",
    "left_wrist_roll_joint",
)

#: The mirror chain. Not driven by this skill, but held at its rest pose so it
#: does not sag into the workspace.
ARM_JOINTS_RIGHT = tuple(n.replace("left_", "right_") for n in ARM_JOINTS_LEFT)

#: Waist joints. Held still: the arm alone covers the workspace, and letting
#: the torso move would put the IK plan and the executed pose in different
#: frames unless both engines agreed on it exactly.
WAIST_JOINTS = ("waist_yaw_joint", "waist_pitch_joint", "waist_roll_joint")

#: Head joints, held still.
HEAD_JOINTS = ("head_yaw_joint", "head_pitch_joint")

#: Body whose frame the policy treats as the end effector.
END_EFFECTOR_BODY = "left_wrist_roll_link"

#: Height the base is welded at, taken from where MuJoCo puts `pelvis` when
#: the model stands on its own legs. Both engines and the IK planner must
#: agree on this or they are planning in different frames -- Drake defaults to
#: welding at the origin, which leaves the planner 0.68m below the simulator
#: and makes every solved configuration quietly wrong.
BASE_HEIGHT = 0.68


def urdf_to_mujoco(name: str) -> str:
    """Translate a URDF joint name to its MuJoCo actuator name.

    AgiBot prefixes actuators with `motor_`. The rule is mechanical, so it
    lives in one function rather than a lookup table that could drift away
    from the models it describes.
    """
    return f"motor_{name}"


@dataclass(frozen=True)
class Observation:
    """One engine-independent snapshot of the world."""

    t: float
    """Simulated seconds since reset."""

    joint_pos: dict[str, float]
    """Position of every controllable joint, keyed by URDF name."""

    ee_pos: np.ndarray
    """End-effector position in world coordinates, shape (3,)."""

    ee_rot: np.ndarray
    """End-effector rotation matrix, shape (3, 3)."""

    object_pos: np.ndarray
    """Manipulated object position in world coordinates, shape (3,)."""

    hand_contacts: int
    """Number of contacts between hand geometry and the object."""

    grasp_force: float
    """Total normal force across those contacts, in newtons."""

    self_collision: bool
    """True if something other than the hand or the work surface is moving
    the object."""

    extras: dict[str, float] = field(default_factory=dict)
    """Back-end specific diagnostics; never read by the policy."""


class SimEnv:
    """Interface both back ends implement.

    Implementations must be deterministic given the same target, so that a
    sim-to-sim disagreement is attributable to engine physics rather than to
    nondeterminism in the harness.
    """

    #: Control period in seconds. Both back ends must agree on this so the
    #: policy sees the same decision rate regardless of engine.
    control_dt: float = 0.01

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def goal(self) -> np.ndarray:
        """Commanded destination on the work surface, shape (3,)."""
        raise NotImplementedError

    @property
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        """Position limit of every controllable joint, keyed by URDF name.

        The policy clamps its own commands rather than relying on the engine
        to saturate them, so that both engines receive identical targets and
        any divergence is purely dynamical.
        """
        raise NotImplementedError

    def reset(self) -> Observation:
        """Return the world to its start state and report the first observation."""
        raise NotImplementedError

    def step(self, targets: dict[str, float]) -> Observation:
        """Hold `targets` for one control period and report the result."""
        raise NotImplementedError

    def render(self, width: int = 640, height: int = 480) -> np.ndarray:
        """Return an RGB frame, for the required demo recording."""
        raise NotImplementedError

    def close(self) -> None:
        """Release engine resources. Safe to call more than once."""

    def __enter__(self) -> "SimEnv":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
