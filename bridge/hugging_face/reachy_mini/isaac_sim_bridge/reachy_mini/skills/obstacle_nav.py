"""Policy-driven obstacle navigation for Hugging Face Reachy Mini.

Uses a potential field planner to navigate around obstacles to a goal position.
This is NOT a replay — the policy computes velocities based on sensor state.

Reachy Mini is a small desktop robot with the following constraints:
  - Linear:  vx ∈ [0, 0.5] m/s  (forward-only base locomotion)
  - Angular: wz ∈ [-0.3, 0.3] rad/s

Metrics reported:
  - position: current (x, y)
  - target: goal (x, y)
  - distance_to_goal: euclidean distance
  - path_progress: 0.0 to 1.0
  - collision_count: number of collisions
  - obstacle_clearance: min distance to nearest obstacle
  - status: idle | navigating | goal_reached | collision | failed
"""
import math
from typing import Any, Dict, List, Tuple

from .base import RobotSkill, SkillResult


class ObstacleNavSkill(RobotSkill):
    """Potential field obstacle navigation policy.

    The robot base is attracted to the goal and repelled by obstacles.
    Forces are combined to produce velocity commands.

    Reachy Mini constraints:
      - No reverse base travel (vx >= 0)
      - max_speed: 0.5 m/s
      - max_angular: 0.3 rad/s
    """

    def __init__(
        self,
        goal_threshold: float = 0.3,
        max_speed: float = 0.5,
        max_angular: float = 0.3,
        attract_gain: float = 1.0,
        repulse_gain: float = 0.8,
        repulse_radius: float = 1.5,
    ):
        self._goal_threshold = goal_threshold
        self._max_speed = min(max_speed, 0.5)  # Reachy Mini hard limit
        self._max_angular = min(max_angular, 0.3)  # Reachy Mini hard limit
        self._attract_gain = attract_gain
        self._repulse_gain = repulse_gain
        self._repulse_radius = repulse_radius

        # State
        self._goal: Tuple[float, float] = (0.0, 0.0)
        self._start: Tuple[float, float] = (0.0, 0.0)
        self._obstacles: List[Tuple[float, float]] = []
        self._collision_count: int = 0
        self._total_distance: float = 0.0
        self._status: str = "idle"
        self._step_count: int = 0

    @property
    def name(self) -> str:
        return "obstacle_navigation"

    def reset(self, params: Dict[str, Any]) -> None:
        """Reset skill with navigation parameters.

        Expected params:
            - goal: (x, y) target position
            - start: (x, y) starting position (default: origin)
            - obstacles: list of (x, y) obstacle positions
        """
        self._goal = tuple(params.get("goal", (5.0, 5.0)))
        self._start = tuple(params.get("start", (0.0, 0.0)))
        self._obstacles = [tuple(obs) for obs in params.get("obstacles", [])]
        self._collision_count = 0
        self._step_count = 0
        self._total_distance = math.dist(self._start, self._goal)
        self._status = "navigating"

    def step(self, state: Dict[str, Any]) -> SkillResult:
        """Compute navigation command using potential field method.

        Args:
            state: Must contain 'position' (x, y) and optionally 'heading'.
                   May contain 'collision' flag.
        """
        self._step_count += 1
        pos = tuple(state.get("position", (0.0, 0.0)))
        heading = state.get("heading", 0.0)
        collision = state.get("collision", False)

        if collision:
            self._collision_count += 1

        # Check goal reached
        dist_to_goal = math.dist(pos, self._goal)
        if dist_to_goal < self._goal_threshold:
            self._status = "goal_reached"
            return SkillResult(
                action="stop",
                speed=0.0,
                metrics=self._build_metrics(pos, dist_to_goal),
                status="success",
            )

        # Check step limit (prevent infinite runs)
        if self._step_count > 1000:
            self._status = "timeout"
            return SkillResult(
                action="stop",
                speed=0.0,
                metrics=self._build_metrics(pos, dist_to_goal),
                status="failed",
            )

        # Compute attractive force toward goal
        dx_goal = self._goal[0] - pos[0]
        dy_goal = self._goal[1] - pos[1]
        dist = math.hypot(dx_goal, dy_goal)
        if dist > 0:
            attract_x = self._attract_gain * dx_goal / dist
            attract_y = self._attract_gain * dy_goal / dist
        else:
            attract_x, attract_y = 0.0, 0.0

        # Compute repulsive forces from obstacles
        repulse_x, repulse_y = 0.0, 0.0
        min_obstacle_dist = float("inf")
        for obs in self._obstacles:
            dx_obs = pos[0] - obs[0]
            dy_obs = pos[1] - obs[1]
            obs_dist = math.hypot(dx_obs, dy_obs)
            min_obstacle_dist = min(min_obstacle_dist, obs_dist)

            if obs_dist < self._repulse_radius and obs_dist > 0.01:
                # Repulsive force inversely proportional to distance squared
                force = self._repulse_gain * (1.0 / obs_dist - 1.0 / self._repulse_radius) / (obs_dist ** 2)
                repulse_x += force * dx_obs / obs_dist
                repulse_y += force * dy_obs / obs_dist

        # Combine forces
        total_x = attract_x + repulse_x
        total_y = attract_y + repulse_y
        total_mag = math.hypot(total_x, total_y)

        if total_mag > 0:
            total_x /= total_mag
            total_y /= total_mag

        # Convert force direction to robot-frame velocity
        # Robot faces +x in its local frame
        desired_angle = math.atan2(total_y, total_x)
        angle_error = desired_angle - heading

        # Normalize angle error to [-pi, pi]
        while angle_error > math.pi:
            angle_error -= 2 * math.pi
        while angle_error < -math.pi:
            angle_error += 2 * math.pi

        # Determine action based on angle error
        # Reachy Mini: forward-only base motion (vx >= 0)
        if abs(angle_error) < 0.3:
            # Goal is roughly ahead — move forward
            action = "move_forward"
            speed = min(self._max_speed, 0.3 + 0.2 * (1.0 - abs(angle_error)))
            angular = 0.0
        elif angle_error > 0:
            # Goal is to the left — rotate base
            action = "turn_left"
            speed = 0.1  # Minimal forward while turning for stability
            angular = min(self._max_angular, 0.5 * angle_error)
        else:
            # Goal is to the right — rotate base
            action = "turn_right"
            speed = 0.1
            angular = max(-self._max_angular, 0.5 * angle_error)

        # Slow down near obstacles (small robot needs more caution)
        if min_obstacle_dist < 1.0:
            speed *= 0.5

        # Reachy Mini cannot reverse base — clamp to non-negative
        speed = max(0.0, speed)

        self._status = "navigating"
        return SkillResult(
            action=action,
            speed=speed,
            angular_speed=angular,
            metrics=self._build_metrics(pos, dist_to_goal, min_obstacle_dist),
            status="running",
        )

    def is_complete(self) -> bool:
        return self._status in ("goal_reached", "timeout", "failed")

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "collision_count": self._collision_count,
            "step_count": self._step_count,
        }

    def _build_metrics(
        self,
        pos: Tuple[float, float],
        dist_to_goal: float,
        min_obstacle_dist: float = float("inf"),
    ) -> Dict[str, Any]:
        progress = 1.0 - (dist_to_goal / self._total_distance) if self._total_distance > 0 else 1.0
        progress = max(0.0, min(1.0, progress))

        return {
            "position": {"x": round(pos[0], 3), "y": round(pos[1], 3)},
            "target": {"x": self._goal[0], "y": self._goal[1]},
            "distance_to_goal": round(dist_to_goal, 3),
            "path_progress": round(progress, 3),
            "collision_count": self._collision_count,
            "obstacle_clearance": round(min_obstacle_dist, 3) if min_obstacle_dist != float("inf") else None,
            "step_count": self._step_count,
            "status": self._status,
        }
