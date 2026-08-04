"""Pure-Python, simulator-independent Spot obstacle-avoidance policy.

Both simulators execute this module for waypoint planning, state feedback,
gait phase, leg pairing, and steering.  Engine adapters only translate these
joint-relative signals to their model's calibrated joint zero positions.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass


POLICY_ID = "spot-obstacle-policy-v2-shared"


def _bounded_env(name: str, default: float, minimum: float, maximum: float) -> float:
    """Load a policy override without allowing it to bypass safety bounds."""

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number between {minimum} and {maximum}.") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}.")
    return value


def wrap_angle(angle: float) -> float:
    """Normalize an angle to [-pi, pi)."""

    return (angle + math.pi) % (2.0 * math.pi) - math.pi


@dataclass(frozen=True)
class Waypoint:
    x: float
    y: float


@dataclass(frozen=True)
class GaitPlan:
    """Joint-relative signals shared across simulators for one control tick."""

    phase: str
    goal_distance: float
    heading_error_rad: float
    steering: float
    active_waypoint: int
    hip_rotation_offsets: tuple[float, float, float, float]
    elbow_lift_offsets: tuple[float, float, float, float]


class SpotObstacleControlCore:
    """Closed-loop planner and diagonal gait, independent of a physics engine."""

    GROUND_CLEARANCE = 0.40
    WAYPOINT_RADIUS = 0.40
    GAIT_FREQUENCY_HZ = 1.0
    HIP_STROKE_RAD = 0.10
    KNEE_LIFT_RAD = 0.20
    HIP_SWING_BIAS_RAD = -0.05
    # Applied in both engines.  The modest gain/limit keeps the untrained
    # open-loop gait stable while the measured pose continues to close the
    # navigation loop.
    STEER_GAIN = 0.40
    STEER_LIMIT = 0.30
    SETTLE_SECONDS = 2.0

    def __init__(
        self,
        goal: tuple[float, float],
        side: str = "left",
        reference_route: tuple[tuple[float, float], ...] | None = None,
        speed_scale: float = 1.0,
    ):
        if isinstance(speed_scale, bool) or not 0.25 <= float(speed_scale) <= 1.0:
            raise ValueError("speed_scale must be between 0.25 and 1.0.")
        self.goal = Waypoint(*goal)
        self.side = side
        self.reference_route = reference_route
        self.speed_scale = float(speed_scale)
        # These are intentionally read once at construction. A validation may
        # tune the common policy, but it must pass the same values to both
        # engine adapters and those values are written to each result.
        self.ground_clearance = _bounded_env(
            "SPOT_POLICY_GROUND_CLEARANCE", self.GROUND_CLEARANCE, 0.35, 0.75
        )
        self.steer_gain = _bounded_env(
            "SPOT_POLICY_STEER_GAIN", self.STEER_GAIN, 0.05, self.STEER_GAIN
        )
        self.steer_limit = _bounded_env(
            "SPOT_POLICY_STEER_LIMIT", self.STEER_LIMIT, 0.05, self.STEER_LIMIT
        )
        self.settle_seconds = _bounded_env(
            "SPOT_POLICY_SETTLE_SECONDS", self.SETTLE_SECONDS, 1.0, 5.0
        )
        self._waypoints: list[Waypoint] = []
        self._waypoint_index = 0
        self.phase = "IDLE"

    def reset(self, start: tuple[float, float], obstacles: tuple[dict, ...] | list[dict]) -> None:
        """Plan the same conservative corridor from the measured reset pose."""

        if self.side not in {"left", "right"}:
            raise ValueError("side must be 'left' or 'right'")
        sign = 1.0 if self.side == "left" else -1.0
        if self.reference_route is not None:
            if self.reference_route[-1] != (self.goal.x, self.goal.y):
                raise ValueError("reference_route must end at goal")
            self._waypoints = [Waypoint(*waypoint) for waypoint in self.reference_route]
        else:
            obstacle = obstacles[0]
            clearance_y = obstacle["y"] + sign * (obstacle["half_y"] + self.ground_clearance)
            lead_in_x = min(obstacle["x"] - 0.80, (start[0] + obstacle["x"]) / 2.0)
            lead_out_x = obstacle["x"] + obstacle["half_x"] + 0.45
            self._waypoints = [
                Waypoint(lead_in_x, clearance_y),
                Waypoint(lead_out_x, clearance_y),
                self.goal,
            ]
        self._waypoint_index = 0
        self.phase = "NAVIGATING"

    @property
    def waypoint_count(self) -> int:
        return len(self._waypoints)

    @property
    def waypoints_completed(self) -> int:
        return self._waypoint_index

    @property
    def route(self) -> tuple[Waypoint, ...]:
        return tuple(self._waypoints)

    def _advance_waypoint(self, x: float, y: float) -> Waypoint:
        target = self._waypoints[self._waypoint_index]
        distance = math.hypot(target.x - x, target.y - y)
        if distance <= self.WAYPOINT_RADIUS and self._waypoint_index < len(self._waypoints) - 1:
            self._waypoint_index += 1
            target = self._waypoints[self._waypoint_index]
        return target

    def compute_plan(self, observation: dict) -> GaitPlan:
        """Compute the shared feedback plan from measured pose and sim time."""

        x, y = observation["position"][:2]
        heading = float(observation["yaw"])
        target = self._advance_waypoint(float(x), float(y))
        goal_distance = math.hypot(self.goal.x - x, self.goal.y - y)

        if self._waypoint_index == len(self._waypoints) - 1 and goal_distance <= self.WAYPOINT_RADIUS:
            self.phase = "GOAL_REACHED"
            return GaitPlan(self.phase, goal_distance, 0.0, 0.0, self._waypoint_index, (0.0,) * 4, (0.0,) * 4)

        desired_heading = math.atan2(target.y - y, target.x - x)
        heading_error = wrap_angle(desired_heading - heading)
        # Positive differential hip stroke turns right in both calibrated adapters.
        steering = max(-self.steer_limit, min(self.steer_limit, -self.steer_gain * heading_error))
        sim_time = float(observation["sim_time"])
        if sim_time < self.settle_seconds:
            return GaitPlan(
                self.phase,
                goal_distance,
                heading_error,
                steering,
                self._waypoint_index,
                (0.0,) * 4,
                (0.0,) * 4,
            )
        phase_time = 2.0 * math.pi * self.GAIT_FREQUENCY_HZ * self.speed_scale * sim_time
        hip_offsets = []
        elbow_offsets = []
        for leg in range(4):
            phase_offset = 0.0 if leg in (0, 3) else math.pi
            sine = math.sin(phase_time + phase_offset)
            side_sign = 1.0 if leg in (0, 2) else -1.0
            hip_offsets.append(
                self.HIP_STROKE_RAD * (1.0 + steering * side_sign) * sine
                + self.HIP_SWING_BIAS_RAD * max(0.0, sine)
            )
            elbow_offsets.append(self.KNEE_LIFT_RAD * max(0.0, sine))

        return GaitPlan(
            self.phase,
            goal_distance,
            heading_error,
            steering,
            self._waypoint_index,
            tuple(hip_offsets),
            tuple(elbow_offsets),
        )

    def diagnostics(self, plan: GaitPlan) -> dict:
        return {
            "policy_id": POLICY_ID,
            "phase": plan.phase,
            "goal_distance": plan.goal_distance,
            "heading_error_rad": plan.heading_error_rad,
            "steering": plan.steering,
            "active_waypoint": plan.active_waypoint,
            "parameters": {
                "ground_clearance_m": self.ground_clearance,
                "steer_gain": self.steer_gain,
                "steer_limit": self.steer_limit,
                "settle_seconds": self.settle_seconds,
                "speed_scale": self.speed_scale,
                "gait_frequency_hz": self.GAIT_FREQUENCY_HZ * self.speed_scale,
                "max_gait_frequency_hz": self.GAIT_FREQUENCY_HZ,
                "max_hip_stroke_rad": self.HIP_STROKE_RAD,
                "max_knee_lift_rad": self.KNEE_LIFT_RAD,
                "max_steering_rad": self.STEER_LIMIT,
            },
            "route": [{"x": waypoint.x, "y": waypoint.y} for waypoint in self.route],
        }
