"""Simulator state metrics for one action.

The Tier 1 criteria ask for measurable simulator state -- "changes in target
pose, successful grasping, changes in door angle, completion of
obstacle-avoidance paths, collision status" -- rather than a claim that the
robot did something. For a push, the honest measurements are where the object
started, where it ended, how far that is from what the payer asked for, and
whether anything collided on the way.

These are recorded from the simulator's own state, not from the policy's
intentions, so a policy that believes it succeeded while the puck sat still
still produces a failing record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class StageTiming:
    stage: str
    duration: float
    error: float

    def to_json(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "durationSec": round(self.duration, 3),
            "goalDistanceM": round(self.error, 4),
        }


@dataclass
class RunMetrics:
    """Everything measured during one execution."""

    engine: str
    success: bool
    reason: str | None = None

    puck_start: tuple[float, float] = (0.0, 0.0)
    puck_end: tuple[float, float] = (0.0, 0.0)
    goal: tuple[float, float] = (0.0, 0.0)

    #: How far the puck actually travelled.
    displacement: float = 0.0
    #: How close it ended to the commanded destination.
    final_distance: float = 0.0
    #: The tolerance that decided success, echoed so a reader can check it.
    tolerance: float = 0.0

    #: Peak number of simultaneous hand-puck contacts, and the largest normal
    #: force seen. Zero contacts on a "successful" push would be a red flag.
    peak_contacts: int = 0
    peak_contact_force: float = 0.0
    #: True if anything other than the hand pushed the puck around.
    foreign_collision: bool = False

    sim_seconds: float = 0.0
    wall_seconds: float = 0.0
    stages: list[StageTiming] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "success": self.success,
            "reason": self.reason,
            "puckStart": [round(v, 4) for v in self.puck_start],
            "puckEnd": [round(v, 4) for v in self.puck_end],
            "goal": [round(v, 4) for v in self.goal],
            "displacementM": round(self.displacement, 4),
            "finalDistanceM": round(self.final_distance, 4),
            "toleranceM": round(self.tolerance, 4),
            "peakContacts": self.peak_contacts,
            "peakContactForceN": round(self.peak_contact_force, 2),
            "foreignCollision": self.foreign_collision,
            "simSeconds": round(self.sim_seconds, 2),
            "wallSeconds": round(self.wall_seconds, 2),
            "stages": [s.to_json() for s in self.stages],
        }


def default_agreement_tolerance(goal_tolerance: float) -> float:
    """How far apart two successful runs may legitimately finish.

    Both engines stop as soon as the puck is within `goal_tolerance` of the
    destination. Two runs that each satisfy that can sit on opposite sides of
    the goal, so they may differ by twice it without either being wrong. A
    tighter bound would fail runs that both did exactly what was asked -- as
    an earlier 0.05 default did, on a pair that both delivered inside 0.04.
    """
    return 2.0 * goal_tolerance


def compare(a: RunMetrics, b: RunMetrics, tolerance: float | None = None) -> dict[str, Any]:
    """Sim-to-sim agreement between two engines running the same policy.

    Physics engines will not agree to the millimetre and should not be
    expected to. What has to agree is the *outcome*: both must reach the same
    verdict, and both must leave the puck in about the same place. A run where
    one engine delivers the puck and the other does not is a real disagreement
    and is reported as such.
    """
    if tolerance is None:
        tolerance = default_agreement_tolerance(max(a.tolerance, b.tolerance))
    end_gap = float(np.linalg.norm(np.array(a.puck_end) - np.array(b.puck_end)))
    verdict_matches = a.success == b.success
    return {
        "engines": [a.engine, b.engine],
        "verdictMatches": verdict_matches,
        "successA": a.success,
        "successB": b.success,
        "puckEndGapM": round(end_gap, 4),
        "finalDistanceA": round(a.final_distance, 4),
        "finalDistanceB": round(b.final_distance, 4),
        "displacementA": round(a.displacement, 4),
        "displacementB": round(b.displacement, 4),
        "toleranceM": tolerance,
        "agrees": bool(verdict_matches and end_gap <= tolerance),
    }
