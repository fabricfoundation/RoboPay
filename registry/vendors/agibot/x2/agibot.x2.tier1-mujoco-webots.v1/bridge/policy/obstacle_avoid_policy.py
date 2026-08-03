"""Reactive potential-field policy for obstacle-avoidance navigation.

Simulator-agnostic; used identically by both the MuJoCo and Webots runners.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    robot_x: float
    robot_y: float
    target_x: float
    target_y: float
    obstacle_positions: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class VelocityCommand:
    vx: float
    vy: float


class ObstacleAvoidPolicy:
    """Attractive-to-target + repulsive-from-obstacle potential field."""

    def __init__(
        self,
        *,
        max_speed: float = 0.5,
        obstacle_influence_radius: float = 0.6,
        repulsion_gain: float = 1.2,
        attraction_gain: float = 1.0,
    ) -> None:
        self.max_speed = max_speed
        self.obstacle_influence_radius = obstacle_influence_radius
        self.repulsion_gain = repulsion_gain
        self.attraction_gain = attraction_gain

    def act(self, obs: Observation) -> VelocityCommand:
        dx = obs.target_x - obs.robot_x
        dy = obs.target_y - obs.robot_y
        dist_to_target = math.hypot(dx, dy) or 1e-6
        ax = self.attraction_gain * dx / dist_to_target
        ay = self.attraction_gain * dy / dist_to_target

        rx = ry = 0.0
        for ox, oy in obs.obstacle_positions:
            ddx = obs.robot_x - ox
            ddy = obs.robot_y - oy
            dist = math.hypot(ddx, ddy) or 1e-6
            if dist < self.obstacle_influence_radius:
                strength = self.repulsion_gain * (
                    1.0 / dist - 1.0 / self.obstacle_influence_radius
                ) / (dist ** 2)
                rx += strength * ddx / dist
                ry += strength * ddy / dist

        vx, vy = ax + rx, ay + ry
        speed = math.hypot(vx, vy)
        if speed > self.max_speed:
            scale = self.max_speed / speed
            vx *= scale
            vy *= scale
        return VelocityCommand(vx=vx, vy=vy)

    @staticmethod
    def reached_target(obs: Observation, *, tolerance: float = 0.15) -> bool:
        return math.hypot(obs.target_x - obs.robot_x, obs.target_y - obs.robot_y) <= tolerance
