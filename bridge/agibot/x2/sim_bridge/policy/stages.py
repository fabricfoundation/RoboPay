"""Stage definitions for the X2 push-to-target plan.

The plan is rise, travel, descend, push -- rather than a direct move to the
contact pose. A straight Cartesian line from the arm's rest pose to a point
beside the puck passes through the puck itself, and the hand sweeps it away
before the push ever starts. Rising first keeps the path clear until the arm
is deliberately placed behind the object.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Stage(Enum):
    RAISE = "raise"
    """Lift the hand to hover altitude, clear of the work surface."""

    TRAVERSE = "traverse"
    """Move horizontally until the hand is above the contact point."""

    DESCEND = "descend"
    """Lower to surface height, behind the puck relative to the goal."""

    PUSH = "push"
    """Sweep along the puck-to-goal line, carrying the puck with it."""

    DONE = "done"
    FAILED = "failed"


#: Order the plan executes in, terminal states excluded.
PLAN: tuple[Stage, ...] = (Stage.RAISE, Stage.TRAVERSE, Stage.DESCEND, Stage.PUSH)


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
