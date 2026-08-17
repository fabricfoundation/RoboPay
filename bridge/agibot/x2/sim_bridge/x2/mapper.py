"""The skill catalogue: what this robot sells, and what it refuses.

Three skills are published, and the third is not padding:

  * ``push_to_target`` -- the paid skill the bounty demo exercises.
  * ``stop`` -- free, always available, so the price list is not uniformly
    "pay me" and a caller can always halt the robot.
  * ``diagnostic_fail`` -- paid, and guaranteed to fail during execution.

That last one exists because the acceptance criteria require a demonstrable
failure path and explicitly rule out "a success-only demo with no failure
path". It is the cleanest way to prove the property that actually matters:
a paid action that fails must return an error and must *not* settle.

Parameter ranges are measured, not guessed. A sweep over the work surface put
delivery at 3/16 before the push stroke was extended past the goal, 9/16
after, and 17/18 once the ranges below were tightened to the band that
actually works. The binding constraint is the *destination*: a goal beyond
y=0.32 leaves the arm's reachable set, so the skill refuses it up front
rather than accepting payment for a motion it cannot complete.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .action_contract import ActionEnvelope, ActionRejected, RejectionCode


@dataclass(frozen=True)
class Bound:
    """Inclusive numeric range for one parameter."""

    low: float
    high: float

    def check(self, name: str, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ActionRejected(
                RejectionCode.PARAMS_OUT_OF_RANGE,
                f"{name} must be a number, got {value!r}",
            ) from exc
        if not math.isfinite(number) or not (self.low <= number <= self.high):
            raise ActionRejected(
                RejectionCode.PARAMS_OUT_OF_RANGE,
                f"{name}={number} is outside the reachable range "
                f"[{self.low}, {self.high}]",
            )
        return number

    def as_json(self) -> dict[str, float]:
        return {"min": self.low, "max": self.high}


@dataclass(frozen=True)
class SkillSpec:
    """One purchasable capability."""

    skill_id: str
    description: str
    price_usdc: str
    payment_required: bool
    params: dict[str, Bound] = field(default_factory=dict)

    def catalogue_entry(self, robot_id: str) -> dict[str, Any]:
        """The discovery record a payer reads before deciding to buy."""
        return {
            "name": self.skill_id,
            "description": self.description,
            "priceUSDC": self.price_usdc,
            "paymentRequired": self.payment_required,
            "paramsSchema": {
                name: {"type": "number", **bound.as_json()}
                for name, bound in self.params.items()
            },
            "robotId": robot_id,
        }


#: Operating envelope, measured on the fixed-base X2 by sweeping targets
#: through *both* engines and keeping only what both deliver.
#:
#: The binding limit is not the arm's reach. The tool point sits 165mm below
#: the wrist, so the hover leg needs the wrist near shoulder height, and
#: hover-reachable ground shrinks to a band roughly 60mm wide in x while push
#: height covers most of the table. Outside that band the puck still moves,
#: but it leaves the two engines disagreeing, which is worse than declining
#: the request: a skill that quotes a price should only quote it where it
#: delivers. Advertising this narrower box is what makes the sim-to-sim
#: agreement inside it meaningful.
#:
#: Measured failures at the edges, all confirmed to be geometric rather than
#: tuning -- loosening the re-approach tolerance and raising the retry cap
#: changed the final distance by under 5mm:
#:   * puck_y <= 0.15 puts the contact point at the edge of the reachable
#:     band, the hand arrives at a sharp angle and knocks the puck sideways.
#:   * puck_x >= 0.275 loses the puck off the committed push line in Drake.
PUCK_X = Bound(0.255, 0.270)
PUCK_Y = Bound(0.160, 0.200)
GOAL_X = Bound(0.255, 0.285)
#: The hard edge. Beyond this the IK reports infeasible mid-motion.
GOAL_Y = Bound(0.290, 0.320)

#: Shorter than this and the puck is already inside the goal tolerance;
#: longer and the destination leaves the reachable set.
MIN_PUSH = 0.10
MAX_PUSH = 0.17


SKILLS: dict[str, SkillSpec] = {
    "push_to_target": SkillSpec(
        skill_id="push_to_target",
        description=(
            "Reach over a puck at (puck_x, puck_y) on the work surface and "
            "push it to (goal_x, goal_y) with the left wrist."
        ),
        price_usdc="0.01",
        payment_required=True,
        params={
            "puck_x": PUCK_X,
            "puck_y": PUCK_Y,
            "goal_x": GOAL_X,
            "goal_y": GOAL_Y,
        },
    ),
    "stop": SkillSpec(
        skill_id="stop",
        description="Hold the current pose and stop all motion immediately.",
        price_usdc="0.00",
        payment_required=False,
    ),
    "diagnostic_fail": SkillSpec(
        skill_id="diagnostic_fail",
        description=(
            "Deliberately fails during execution. Exists so the "
            "no-settle-on-failure guarantee can be exercised on demand."
        ),
        price_usdc="0.01",
        payment_required=True,
    ),
}


@dataclass(frozen=True)
class TaskSpec:
    """A validated request, ready for the simulator."""

    skill_id: str
    puck_xy: tuple[float, float] | None = None
    goal_xy: tuple[float, float] | None = None
    #: True for skills that must report failure no matter what the robot does.
    expect_failure: bool = False


def catalogue(robot_id: str) -> list[dict[str, Any]]:
    """Every skill this robot exposes, for pre-purchase discovery."""
    return [spec.catalogue_entry(robot_id) for spec in SKILLS.values()]


def resolve(envelope: ActionEnvelope) -> TaskSpec:
    """Validate an envelope's skill and parameters, or reject it."""
    spec = SKILLS.get(envelope.skill_id)
    if spec is None:
        raise ActionRejected(
            RejectionCode.UNKNOWN_SKILL,
            f"no such skill {envelope.skill_id!r}; this robot offers "
            f"{', '.join(sorted(SKILLS))}",
        )

    if spec.skill_id == "stop":
        return TaskSpec(skill_id=spec.skill_id)
    if spec.skill_id == "diagnostic_fail":
        return TaskSpec(skill_id=spec.skill_id, expect_failure=True)

    missing = [name for name in spec.params if name not in envelope.params]
    if missing:
        raise ActionRejected(
            RejectionCode.PARAMS_OUT_OF_RANGE,
            f"missing parameter(s): {', '.join(missing)}",
        )
    values = {
        name: bound.check(name, envelope.params[name])
        for name, bound in spec.params.items()
    }
    puck = (values["puck_x"], values["puck_y"])
    goal = (values["goal_x"], values["goal_y"])
    distance = math.dist(puck, goal)
    if not (MIN_PUSH <= distance <= MAX_PUSH):
        raise ActionRejected(
            RejectionCode.PARAMS_OUT_OF_RANGE,
            f"push distance {distance:.3f}m is outside [{MIN_PUSH}, {MAX_PUSH}]",
        )
    return TaskSpec(skill_id=spec.skill_id, puck_xy=puck, goal_xy=goal)
