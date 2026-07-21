"""Skill manager for routing actions to policy-driven skills."""
import json
import logging
from typing import Any, Dict, Optional

from zenoh_bridge import ActionEvent

from .base import RobotSkill, SkillResult
from .obstacle_nav import ObstacleNavSkill

logger = logging.getLogger(__name__)

# Registry of available skills
SKILL_REGISTRY: Dict[str, type] = {
    "obstacle_navigation": ObstacleNavSkill,
}


class SkillManager:
    """Manages skill lifecycle and routes actions to skills.

    When a skill-based action arrives (navigate_to, pick, place),
    the manager activates the appropriate skill and runs it step-by-step
    until completion.
    """

    def __init__(self, metrics_callback=None):
        self._active_skill: Optional[RobotSkill] = None
        self._metrics_callback = metrics_callback

    @property
    def is_active(self) -> bool:
        return self._active_skill is not None and not self._active_skill.is_complete()

    def execute(self, event: ActionEvent) -> Dict[str, Any]:
        """Execute a skill-based action.

        Returns metrics dict for publishing.
        """
        action = event.action
        params = event.params

        if action == "navigate_to":
            return self._start_navigation(params)
        elif action == "skill_step":
            return self._step_skill(params)
        elif action == "skill_cancel":
            return self._cancel_skill()
        else:
            return {"error": f"Unknown skill action: {action}"}

    def _start_navigation(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Start obstacle navigation skill."""
        goal = params.get("goal")
        if not goal:
            return {"error": "navigate_to requires 'goal' parameter"}

        obstacles = params.get("obstacles", [])
        start = params.get("start", [0.0, 0.0])

        skill = ObstacleNavSkill(
            goal_threshold=params.get("goal_threshold", 0.3),
            max_speed=params.get("max_speed", 0.5),
        )
        skill.reset({
            "goal": goal,
            "start": start,
            "obstacles": obstacles,
        })
        self._active_skill = skill

        logger.info(
            "Started obstacle_navigation: goal=%s obstacles=%s",
            goal, obstacles,
        )
        return {
            "skill": "obstacle_navigation",
            "status": "started",
            "metrics": skill.get_metrics(),
        }

    def _step_skill(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Run one step of the active skill."""
        if not self._active_skill:
            return {"error": "No active skill"}

        state = params.get("state", {})
        result = self._active_skill.step(state)

        if self._metrics_callback:
            self._metrics_callback(result.metrics)

        return {
            "skill": self._active_skill.name,
            "action": result.action,
            "speed": result.speed,
            "angular_speed": result.angular_speed,
            "metrics": result.metrics,
            "status": result.status,
        }

    def _cancel_skill(self) -> Dict[str, Any]:
        """Cancel the active skill."""
        name = self._active_skill.name if self._active_skill else "none"
        self._active_skill = None
        return {"skill": name, "status": "cancelled"}

    def get_active_skill(self) -> Optional[RobotSkill]:
        return self._active_skill
