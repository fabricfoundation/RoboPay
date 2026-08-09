"""Shared, online Go2 planner and foot-space gait generator.

The same state-feedback planner and gait phase run in MuJoCo and Webots.  The
engine adapters only turn the requested foot positions into their actuator
API; neither adapter writes the floating base pose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


POLICY_ID = "unitree-go2-online-footspace-trot-v1"


def wrap_angle(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True)
class Waypoint:
    x: float
    y: float


@dataclass(frozen=True)
class GaitPlan:
    phase: str
    goal_distance: float
    heading_error_rad: float
    steering: float
    active_waypoint: int
    foot_x_m: tuple[float, float, float, float]
    foot_z_m: tuple[float, float, float, float]


class Go2ObstacleControlCore:
    """Pure-Python task planner driven by measured simulator state."""

    WAYPOINT_RADIUS_M = 0.30
    GOAL_RADIUS_M = 0.32
    SETTLE_SECONDS = 1.0
    GAIT_FREQUENCY_HZ = 1.25
    SWING_FRACTION = 0.28
    STRIDE_HALF_LENGTH_M = 0.09
    STANCE_HEIGHT_M = -0.285
    SWING_LIFT_M = 0.09
    STEER_GAIN = 0.10
    STEER_LIMIT = 0.06

    def __init__(
        self,
        goal: tuple[float, float],
        side: str = "left",
        reference_route: tuple[tuple[float, float], ...] | None = None,
        speed_scale: float = 1.0,
    ) -> None:
        if side != "left":
            raise ValueError("The validated Go2 corridor uses side='left'.")
        if isinstance(speed_scale, bool) or not 0.5 <= float(speed_scale) <= 1.0:
            raise ValueError("speed_scale must be between 0.5 and 1.0.")
        self.goal = Waypoint(*goal)
        self.side = side
        self.speed_scale = float(speed_scale)
        self._route = tuple(Waypoint(*item) for item in (reference_route or (goal,)))
        if self._route[-1] != self.goal:
            raise ValueError("reference_route must end at goal")
        self._waypoint_index = 0
        self.phase = "IDLE"

    def reset(self, start: tuple[float, float], obstacles: tuple[dict, ...] | list[dict]) -> None:
        del start, obstacles
        self._waypoint_index = 0
        self.phase = "NAVIGATING"

    @property
    def waypoint_count(self) -> int:
        return len(self._route)

    @property
    def waypoints_completed(self) -> int:
        return self._waypoint_index

    @property
    def route(self) -> tuple[Waypoint, ...]:
        return self._route

    def _target(self, x: float, y: float) -> Waypoint:
        target = self._route[self._waypoint_index]
        if (
            math.hypot(target.x - x, target.y - y) <= self.WAYPOINT_RADIUS_M
            and self._waypoint_index < len(self._route) - 1
        ):
            self._waypoint_index += 1
            target = self._route[self._waypoint_index]
        return target

    def compute_plan(self, observation: dict) -> GaitPlan:
        x, y = map(float, observation["position"][:2])
        target = self._target(x, y)
        goal_distance = math.hypot(self.goal.x - x, self.goal.y - y)
        if self._waypoint_index == len(self._route) - 1 and goal_distance <= self.GOAL_RADIUS_M:
            self.phase = "GOAL_REACHED"
            return GaitPlan(self.phase, goal_distance, 0.0, 0.0, self._waypoint_index, (0.0,) * 4, (self.STANCE_HEIGHT_M,) * 4)

        heading_error = wrap_angle(
            math.atan2(target.y - y, target.x - x) - float(observation["yaw"])
        )
        # In the calibrated Go2 gait a negative differential turns left.
        steering = max(-self.STEER_LIMIT, min(self.STEER_LIMIT, -self.STEER_GAIN * heading_error))
        t = float(observation["sim_time"])
        foot_x: list[float] = []
        foot_z: list[float] = []
        for leg in range(4):
            side_sign = 1.0 if leg in (1, 3) else -1.0  # FL/RL are left.
            stride = self.STRIDE_HALF_LENGTH_M * (1.0 + steering * side_sign)
            u = (
                self.GAIT_FREQUENCY_HZ * self.speed_scale * max(0.0, t - self.SETTLE_SECONDS)
                + (0.0 if leg in (0, 3) else 0.5)
            ) % 1.0
            if t < self.SETTLE_SECONDS:
                x_cmd, z_cmd = 0.0, self.STANCE_HEIGHT_M
            elif u < self.SWING_FRACTION:
                alpha = u / self.SWING_FRACTION
                x_cmd = -stride + 2.0 * stride * alpha
                z_cmd = self.STANCE_HEIGHT_M + self.SWING_LIFT_M * math.sin(math.pi * alpha)
            else:
                alpha = (u - self.SWING_FRACTION) / (1.0 - self.SWING_FRACTION)
                x_cmd = stride - 2.0 * stride * alpha
                z_cmd = self.STANCE_HEIGHT_M
            foot_x.append(x_cmd)
            foot_z.append(z_cmd)
        return GaitPlan(self.phase, goal_distance, heading_error, steering, self._waypoint_index, tuple(foot_x), tuple(foot_z))

    def diagnostics(self, plan: GaitPlan) -> dict:
        return {
            "policy_id": POLICY_ID,
            "phase": plan.phase,
            "goal_distance": plan.goal_distance,
            "heading_error_rad": plan.heading_error_rad,
            "steering": plan.steering,
            "active_waypoint": plan.active_waypoint,
            "route": [{"x": item.x, "y": item.y} for item in self.route],
            "parameters": {
                "gait_frequency_hz": self.GAIT_FREQUENCY_HZ * self.speed_scale,
                "stride_half_length_m": self.STRIDE_HALF_LENGTH_M,
                "swing_lift_m": self.SWING_LIFT_M,
                "steer_limit": self.STEER_LIMIT,
            },
        }
