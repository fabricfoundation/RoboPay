"""Stage definitions for the push-to-target plan.

The plan is up, across, down, then push -- rather than a direct move to the
contact pose. A straight Cartesian line from the robot's rest pose to a point
beside the puck passes through the puck itself, and the hand sweeps it away
before the push ever starts. Rising first keeps the whole path clear until the
hand is deliberately placed behind the object.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Stage(Enum):
    TURN = "turn"
    """Rotate the waist to face the puck's observed bearing."""

    RAISE = "raise"
    """Lift the hand to hover altitude above the contact point."""

    APPROACH = "approach"
    """Lower the hand to table height, behind the puck relative to the goal."""

    PUSH = "push"
    """Drive the hand along the puck-to-goal line, carrying the puck with it."""

    DONE = "done"
    FAILED = "failed"


#: Order the plan executes in, terminal states excluded.
PLAN: tuple[Stage, ...] = (
    Stage.TURN,
    Stage.RAISE,
    Stage.APPROACH,
    Stage.PUSH,
)


@dataclass
class StageRecord:
    """One completed stage, recorded for the validation report."""

    stage: str
    entered_at: float
    left_at: float
    error: float

    @property
    def duration(self) -> float:
        return self.left_at - self.entered_at
