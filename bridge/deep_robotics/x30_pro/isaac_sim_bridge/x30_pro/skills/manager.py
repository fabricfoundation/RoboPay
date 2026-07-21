"""Skill manager for routing actions to policy-driven skills."""
import logging
from typing import Any, Dict, Optional
from zenoh_bridge import ActionEvent
from .base import RobotSkill, SkillResult
from .obstacle_nav import ObstacleNavSkill

logger = logging.getLogger(__name__)


class SkillManager:
    def __init__(self, metrics_callback=None):
        self._active_skill = None
        self._metrics_callback = metrics_callback

    @property
    def is_active(self): return self._active_skill is not None and not self._active_skill.is_complete()

    def execute(self, event: ActionEvent) -> Dict[str, Any]:
        if event.action == "navigate_to":
            return self._start_navigation(event.params)
        elif event.action == "skill_step":
            return self._step_skill(event.params)
        elif event.action == "skill_cancel":
            return self._cancel_skill()
        return {"error": f"Unknown skill action: {event.action}"}

    def _start_navigation(self, params):
        goal = params.get("goal")
        if not goal: return {"error": "navigate_to requires goal"}
        skill = ObstacleNavSkill()
        skill.reset({"goal": goal, "start": params.get("start", [0, 0]), "obstacles": params.get("obstacles", [])})
        self._active_skill = skill
        return {"skill": "obstacle_navigation", "status": "started"}

    def _step_skill(self, params):
        if not self._active_skill: return {"error": "No active skill"}
        result = self._active_skill.step(params.get("state", {}))
        return {"skill": self._active_skill.name, "action": result.action, "speed": result.speed,
                "angular_speed": result.angular_speed, "metrics": result.metrics, "status": result.status}

    def _cancel_skill(self):
        name = self._active_skill.name if self._active_skill else "none"
        self._active_skill = None
        return {"skill": name, "status": "cancelled"}
