"""Policy-driven obstacle navigation for X30 Pro."""
import math
from typing import Any, Dict, List, Tuple
from .base import RobotSkill, SkillResult


class ObstacleNavSkill(RobotSkill):
    def __init__(self, goal_threshold=0.3, max_speed=0.5):
        self._goal_threshold = goal_threshold
        self._max_speed = max_speed
        self._goal = (0.0, 0.0)
        self._obstacles = []
        self._collision_count = 0
        self._total_distance = 0.0
        self._status = "idle"
        self._step_count = 0

    @property
    def name(self): return "obstacle_navigation"

    def reset(self, params):
        self._goal = tuple(params.get("goal", (5.0, 5.0)))
        start = tuple(params.get("start", (0.0, 0.0)))
        self._obstacles = [tuple(o) for o in params.get("obstacles", [])]
        self._collision_count = 0
        self._step_count = 0
        self._total_distance = math.dist(start, self._goal)
        self._status = "navigating"

    def step(self, state):
        self._step_count += 1
        pos = tuple(state.get("position", (0.0, 0.0)))
        heading = state.get("heading", 0.0)
        if state.get("collision", False):
            self._collision_count += 1

        dist_to_goal = math.dist(pos, self._goal)
        if dist_to_goal < self._goal_threshold:
            self._status = "goal_reached"
            return SkillResult(action="stop", metrics=self._metrics(pos, dist_to_goal), status="success")
        if self._step_count > 1000:
            self._status = "timeout"
            return SkillResult(action="stop", metrics=self._metrics(pos, dist_to_goal), status="failed")

        dx = self._goal[0] - pos[0]
        dy = self._goal[1] - pos[1]
        d = math.hypot(dx, dy)
        if d > 0:
            ax, ay = dx / d, dy / d
        else:
            ax, ay = 0.0, 0.0

        rx, ry = 0.0, 0.0
        for obs in self._obstacles:
            ox, oy = pos[0] - obs[0], pos[1] - obs[1]
            od = math.hypot(ox, oy)
            if 0.01 < od < 1.5:
                f = 0.8 * (1.0 / od - 1.0 / 1.5) / (od ** 2)
                rx += f * ox / od
                ry += f * oy / od

        tx, ty = ax + rx, ay + ry
        tm = math.hypot(tx, ty)
        if tm > 0:
            tx, ty = tx / tm, ty / tm

        angle = math.atan2(ty, tx) - heading
        while angle > math.pi: angle -= 2 * math.pi
        while angle < -math.pi: angle += 2 * math.pi

        if abs(angle) < 0.3:
            return SkillResult(action="move_forward", speed=min(0.5, 0.3 + 0.2 * (1 - abs(angle))), metrics=self._metrics(pos, dist_to_goal))
        elif angle > 0:
            return SkillResult(action="turn_left", speed=0.2, angular_speed=min(0.2, 0.5 * angle), metrics=self._metrics(pos, dist_to_goal))
        else:
            return SkillResult(action="turn_right", speed=0.2, angular_speed=max(-0.2, 0.5 * angle), metrics=self._metrics(pos, dist_to_goal))

    def is_complete(self): return self._status in ("goal_reached", "timeout", "failed")

    def _metrics(self, pos, dist):
        progress = max(0.0, min(1.0, 1.0 - dist / self._total_distance)) if self._total_distance > 0 else 1.0
        return {"position": {"x": round(pos[0], 3), "y": round(pos[1], 3)}, "target": {"x": self._goal[0], "y": self._goal[1]},
                "distance_to_goal": round(dist, 3), "path_progress": round(progress, 3), "collision_count": self._collision_count,
                "step_count": self._step_count, "status": self._status}
