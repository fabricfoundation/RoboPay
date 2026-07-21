"""Policy-driven robot skills for AGIBot X2.

Skills can be imported independently without the full ROS2 stack.
The SkillManager requires zenoh_bridge (ROS2) and is only used at runtime.
"""
from .base import RobotSkill, SkillResult
from .obstacle_nav import ObstacleNavSkill

__all__ = ["RobotSkill", "SkillResult", "ObstacleNavSkill"]


def get_skill_manager():
    """Lazy import of SkillManager (requires zenoh_bridge)."""
    from .manager import SkillManager
    return SkillManager
