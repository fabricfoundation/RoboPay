"""Canonical obstacle course and state-driven route planner."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Obstacle:
    name: str
    x: float
    y: float
    half_x: float
    half_y: float
    height: float
    color: tuple[float, float, float, float]


OBSTACLES = (
    Obstacle("amber_gate", 1.6, 0.52, 0.18, 0.12, 0.62, (1.0, 0.48, 0.04, 1.0)),
    Obstacle("cyan_gate", 3.2, -0.52, 0.18, 0.12, 0.72, (0.05, 0.72, 0.88, 1.0)),
    Obstacle("violet_gate", 4.8, 0.52, 0.18, 0.12, 0.82, (0.58, 0.22, 0.86, 1.0)),
)

WAYPOINTS = (
    (0.70, -0.06),
    (1.50, -0.10),
    (2.10, -0.04),
    (2.55, 0.06),
    (3.20, 0.10),
    (3.80, 0.04),
    (4.15, -0.06),
    (4.80, -0.10),
    (5.30, -0.04),
    (5.60, 0.0),
)
GOAL = WAYPOINTS[-1]
ROBOT_CLEARANCE_RADIUS = 0.24


def wrap_angle(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def obstacle_clearance(x: float, y: float, obstacle: Obstacle) -> float:
    """Signed circular-base clearance to an axis-aligned obstacle."""

    dx = max(abs(x - obstacle.x) - obstacle.half_x, 0.0)
    dy = max(abs(y - obstacle.y) - obstacle.half_y, 0.0)
    outside = math.hypot(dx, dy)
    if dx == 0.0 and dy == 0.0:
        inside = min(
            obstacle.half_x - abs(x - obstacle.x),
            obstacle.half_y - abs(y - obstacle.y),
        )
        return -ROBOT_CLEARANCE_RADIUS - inside
    return outside - ROBOT_CLEARANCE_RADIUS


class RoutePlanner:
    """Online waypoint planner driven only by measured simulator pose."""

    def __init__(self) -> None:
        self.waypoint_index = 0
        self.visited: list[int] = []

    @property
    def complete(self) -> bool:
        return self.waypoint_index >= len(WAYPOINTS)

    def command(self, x: float, y: float, yaw: float) -> tuple[float, float, float]:
        while not self.complete:
            target = WAYPOINTS[self.waypoint_index]
            if math.hypot(target[0] - x, target[1] - y) > 0.24:
                break
            self.visited.append(self.waypoint_index)
            self.waypoint_index += 1
        if self.complete:
            return 0.0, 0.0, 0.0
        tx, ty = WAYPOINTS[self.waypoint_index]
        distance = math.hypot(tx - x, ty - y)
        heading = math.atan2(ty - y, tx - x)
        error = wrap_angle(heading - yaw)
        angular = max(-0.5, min(0.5, 1.1 * error))
        alignment = max(0.0, math.cos(error))
        linear = min(0.65, 0.20 + 0.46 * min(distance, 1.0)) * alignment
        if abs(error) > 0.85:
            linear = 0.0
        elif abs(error) > 0.45:
            linear = min(linear, 0.12)
        return linear, 0.0, angular


def webots_course_vrml() -> str:
    """Render the canonical obstacles and goal into a Webots world."""

    nodes: list[str] = []
    for obstacle in OBSTACLES:
        red, green, blue, _ = obstacle.color
        nodes.append(
            f"""Solid {{
  translation {obstacle.x} {obstacle.y} {obstacle.height / 2.0}
  name \"{obstacle.name}\"
  children [
    Shape {{
      appearance PBRAppearance {{ baseColor {red} {green} {blue} roughness 0.55 metalness 0 }}
      geometry Box {{ size {2 * obstacle.half_x} {2 * obstacle.half_y} {obstacle.height} }}
    }}
  ]
  boundingObject Box {{ size {2 * obstacle.half_x} {2 * obstacle.half_y} {obstacle.height} }}
}}"""
        )
    for index, (x, y) in enumerate(WAYPOINTS):
        nodes.append(
            f"""Transform {{
  translation {x} {y} 0.012
  children [ Shape {{
    appearance PBRAppearance {{ baseColor 0.18 0.82 0.32 transparency 0.28 roughness 0.8 }}
    geometry Cylinder {{ radius 0.10 height 0.018 }}
  }} ]
}}"""
        )
    nodes.append(
        f"""Transform {{
  translation {GOAL[0]} {GOAL[1]} 0.03
  children [ Shape {{
    appearance PBRAppearance {{ baseColor 0.10 0.95 0.25 roughness 0.7 }}
    geometry Cylinder {{ radius 0.28 height 0.04 }}
  }} ]
}}"""
    )
    return "\n".join(nodes)
