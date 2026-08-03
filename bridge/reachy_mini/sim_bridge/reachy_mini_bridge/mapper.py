"""Fabric ActionEvent -> gaze-target command mapping for Reachy Mini.

Reachy Mini has no arms and no wheeled base, so generic Fabric actions like
"move_forward" or "pick" don't apply. Instead we accept a small vocabulary
of gaze/attention actions and turn them into a target the head-tracking
policy can chase.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from zenoh_bridge import ActionEvent


@dataclass
class GazeCommand:
    """A target for the head-tracking policy to pursue."""
    mode: str                       # "track" | "reset" | "hold"
    target_name: Optional[str]      # e.g. "apple", or None for reset/hold
    target_xy: Optional[Tuple[float, float]]  # normalized image-plane coords, if given


class ReachyMiniMapper:
    """Translates Fabric actions into GazeCommand objects.

    Supported actions:
      - "look_at"   params: {"target": <name>} or {"x":.., "y":..}
      - "track"     params: {"target": <name>}
      - "reset_gaze" / "stop"
    Unknown actions fall back to a safe "hold" (freeze current pose).
    """

    def map(self, event: ActionEvent) -> GazeCommand:
        action = event.action
        params = event.params or {}

        if action in ("look_at", "track"):
            target = params.get("target")
            x = params.get("x")
            y = params.get("y")
            xy = (float(x), float(y)) if x is not None and y is not None else None
            if target is None and xy is None:
                # Nothing to track -> treat as hold, never guess a fake target.
                return GazeCommand(mode="hold", target_name=None, target_xy=None)
            return GazeCommand(mode="track", target_name=target, target_xy=xy)

        if action in ("reset_gaze", "stop", "center"):
            return GazeCommand(mode="reset", target_name=None, target_xy=None)

        # Unknown action -> safe default, do not move.
        return GazeCommand(mode="hold", target_name=None, target_xy=None)
