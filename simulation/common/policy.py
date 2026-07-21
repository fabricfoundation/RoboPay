"""
Common policy module shared between MuJoCo and Webots simulators for LimX TRON1.

Ensures identical policy behavior across simulators for Sim-to-Sim validation.
This is the policy-driven component required by Tier 1 bounty.

LimX TRON1 Humanoid:
  - Velocity: vx [0, 0.8], wz [-0.3, 0.3]
"""
import math
from typing import List, Tuple


class PotentialFieldPolicy:
    """Potential field obstacle navigation policy for LimX TRON1.

    NOT a replay — computes velocities from simulator state at each step.
    Uses attractive force toward goal and repulsive forces from obstacles.

    Attributes:
        goal: Target (x, y) position
        obstacles: List of (x, y) obstacle positions
        goal_threshold: Distance to consider goal reached
    """

    # LimX TRON1 velocity limits
    VX_MIN = 0.0
    VX_MAX = 0.8
    WZ_MIN = -0.3
    WZ_MAX = 0.3

    def __init__(
        self,
        goal: Tuple[float, float] = (10.0, 0.0),
        obstacles: List[Tuple[float, float]] = None,
        goal_threshold: float = 0.5,
        attract_gain: float = 2.0,
        repulse_gain: float = 1.5,
        repulse_radius: float = 2.0,
    ):
        self.goal = goal
        self.obstacles = obstacles or []
        self.goal_threshold = goal_threshold
        self.attract_gain = attract_gain
        self.repulse_gain = repulse_gain
        self.repulse_radius = repulse_radius

    def compute_action(
        self,
        pos: Tuple[float, float],
        heading: float,
    ) -> Tuple[float, float]:
        """Compute (forward_speed, angular_speed) from current simulator state.

        This is policy-driven: the output depends on the current state,
        not a predefined trajectory.

        Velocity limits for TRON1:
          vx: [0, 0.8]  (forward only)
          wz: [-0.3, 0.3]

        Args:
            pos: Current (x, y) position from simulator
            heading: Current heading angle in radians from simulator

        Returns:
            (forward_speed, angular_speed) for the robot controller
        """
        # Attractive force toward goal
        dx = self.goal[0] - pos[0]
        dy = self.goal[1] - pos[1]
        dist = math.hypot(dx, dy)

        if dist > 0:
            attract_x = self.attract_gain * dx / dist
            attract_y = self.attract_gain * dy / dist
        else:
            return 0.0, 0.0

        # Repulsive forces from obstacles
        repulse_x, repulse_y = 0.0, 0.0
        for obs in self.obstacles:
            ox = pos[0] - obs[0]
            oy = pos[1] - obs[1]
            od = math.hypot(ox, oy)
            if 0.01 < od < self.repulse_radius:
                force = self.repulse_gain * (1.0 / od - 1.0 / self.repulse_radius) / (od ** 2)
                repulse_x += force * ox / od
                repulse_y += force * oy / od

        # Combined force
        total_x = attract_x + repulse_x
        total_y = attract_y + repulse_y
        total_mag = math.hypot(total_x, total_y)

        if total_mag > 0:
            total_x /= total_mag
            total_y /= total_mag

        # Convert to robot-frame velocities
        desired_angle = math.atan2(total_y, total_x)
        angle_error = desired_angle - heading

        # Normalize to [-pi, pi]
        while angle_error > math.pi:
            angle_error -= 2 * math.pi
        while angle_error < -math.pi:
            angle_error += 2 * math.pi

        # Slow down near obstacles
        min_obs_dist = min(
            [math.hypot(pos[0] - o[0], pos[1] - o[1]) for o in self.obstacles] or [float("inf")]
        )
        base_speed = 0.5
        if min_obs_dist < 1.0:
            base_speed *= 0.5

        forward_speed = base_speed * max(0, math.cos(angle_error))
        angular_speed = 0.5 * angle_error

        # Clamp to TRON1 velocity limits
        forward_speed = max(self.VX_MIN, min(self.VX_MAX, forward_speed))
        angular_speed = max(self.WZ_MIN, min(self.WZ_MAX, angular_speed))

        return forward_speed, angular_speed

    def is_goal_reached(self, pos: Tuple[float, float]) -> bool:
        """Check if robot has reached the goal."""
        return math.hypot(self.goal[0] - pos[0], self.goal[1] - pos[1]) < self.goal_threshold


# Standard test scenarios for validation
SCENARIOS = {
    "straight_line": {
        "start": (0.0, 0.0),
        "goal": (10.0, 0.0),
        "obstacles": [],
        "expected_max_collisions": 0,
        "expected_min_progress": 0.90,
    },
    "single_obstacle": {
        "start": (0.0, 0.0),
        "goal": (10.0, 0.0),
        "obstacles": [(5.0, 2.0)],
        "expected_max_collisions": 0,
        "expected_min_progress": 0.85,
    },
    "corridor": {
        "start": (0.0, 0.0),
        "goal": (10.0, 0.0),
        "obstacles": [(2.5, 0.0), (5.0, 1.5), (7.0, -1.0)],
        "expected_max_collisions": 1,
        "expected_min_progress": 0.80,
    },
}
