"""Simulator-independent geometry for the Atlas shelf-inspection task.

Every simulator (MuJoCo, PyBullet, Webots) builds its scene from the constants
in this module, so the three runs are the same task by construction and their
metrics are directly comparable.

Frame: world coordinates, +x in front of the robot, +y to its left, +z up.
The robot stands at the origin and inspects a shelf with its right hand.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Standing pose held by the legs and left arm for the whole episode.
STANCE_POSE: dict[str, float] = {
    "l_leg_hpy": -0.30, "l_leg_kny": 0.62, "l_leg_aky": -0.32,
    "r_leg_hpy": -0.30, "r_leg_kny": 0.62, "r_leg_aky": -0.32,
    "l_arm_shx": -1.35, "r_arm_shx": 1.35,
    "l_arm_elx": 1.20, "r_arm_elx": -1.20,
    "l_arm_ely": 1.00, "r_arm_ely": 1.00,
}

#: Joints the inspection controller is allowed to move (right arm only).
INSPECTION_CHAIN: tuple[str, ...] = ("r_arm_shz", "r_arm_shx", "r_arm_ely", "r_arm_elx")

#: End effector whose pose is measured against each inspection target.
END_EFFECTOR_BODY = "r_hand"

#: Pose the right hand settles into before the first target, measured from the
#: pinned model.  ``tests/test_model_integrity.py`` fails if the model drifts.
HOME_END_EFFECTOR = (0.3907, -0.5592, 0.9589)
HOME_PELVIS_HEIGHT_M = 0.911


@dataclass(frozen=True)
class InspectionTarget:
    """One shelf position the end effector must reach and hold."""

    name: str
    x: float
    y: float
    z: float
    #: Distance under which the target counts as reached, in metres.
    tolerance_m: float = 0.03
    #: Consecutive control steps the hand must stay inside the tolerance.
    hold_steps: int = 250

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


#: Three shelf points, all inside the conservative reach core measured by
#: ``reach_envelope.py`` and recorded in ``docs/evidence/reach-envelope.json``:
#: 0.06-0.18 m forward and -0.12..+0.20 m vertical of :data:`HOME_END_EFFECTOR`,
#: a block in which every one of the 15 probes reached its target with the robot
#: still standing.  These three sit at 0.13-0.15 m forward and -0.06..+0.06 m
#: vertical, in the open bay between the shelf plates, so a straight-line
#: approach from the home pose never crosses a plate.
INSPECTION_TARGETS: tuple[InspectionTarget, ...] = (
    InspectionTarget("shelf-top", 0.52, -0.56, 1.02),
    InspectionTarget("shelf-middle", 0.54, -0.60, 0.94),
    InspectionTarget("shelf-lower", 0.52, -0.52, 0.90),
)

#: Static shelf structure, drawn as collision geometry in every simulator.
#: ``half`` is the box half-extent in metres.  The plates start at x = 0.62, in
#: front of which the inspection bay stays clear.
SHELF_PARTS: tuple[dict, ...] = (
    {"name": "shelf_back", "pos": (0.87, -0.56, 0.90), "half": (0.02, 0.30, 0.30)},
    {"name": "shelf_upper", "pos": (0.76, -0.56, 1.10), "half": (0.10, 0.30, 0.01)},
    {"name": "shelf_lower", "pos": (0.76, -0.56, 0.66), "half": (0.10, 0.30, 0.01)},
)

#: Pelvis height below which the episode is declared a fall.  Atlas stands at
#: 0.911 m; 0.70 m is unambiguously "no longer standing" rather than the 0.05 m
#: floor-contact threshold used by the previous revision.
FALL_THRESHOLD_M = 0.70

#: Wall-clock budget for the whole inspection sequence, in simulated seconds.
EPISODE_BUDGET_S = 30.0

#: Joint-servo gains, shared by every engine so the servo law is one decision
#: rather than three.  Webots defaults to P=10, which cannot hold a 182 kg
#: humanoid upright, so each backend sets these explicitly.
SERVO_KP = 3000.0
SERVO_KD = 150.0

#: --- payment contract ------------------------------------------------------
#: The skill price, kept here so the profile, the bridge, the demo and the
#: settlement layer cannot drift apart. ``registry/.../payment-policy.yaml``
#: declares the same number and ``tests/test_payment_contract.py`` pins them
#: together.
SKILL_PRICE_USDC = "0.001"
USDC_DECIMALS = 6
#: The same price in the raw integer units an x402 receipt carries.
SKILL_PRICE_RAW = "1000"
#: Circle's USDC on Base Sepolia, and the network the profile settles on.
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
BASE_SEPOLIA_CHAIN_ID = 84532
PAYMENT_NETWORK = "eip155:84532"

#: Webots drives joints through its own implicit position servo, whose gain is a
#: velocity-level term and so is not comparable to :data:`SERVO_KP`.  Measured:
#: P=10 (the Webots default) lets Atlas topple, P>=80 holds the stance.
WEBOTS_SERVO_P = 120.0
