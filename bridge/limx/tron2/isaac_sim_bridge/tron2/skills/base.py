"""Base interface for policy-driven robot skills."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class SkillResult:
    """Result from a skill execution step."""

    action: str
    speed: float = 0.0
    angular_speed: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    status: str = "running"  # running | success | failed | collision


class RobotSkill(ABC):
    """Abstract base class for robot skills.

    A skill is a policy-driven behavior that:
    - Receives high-level goals (e.g., "navigate to position X")
    - Computes low-level commands (velocity, angular velocity)
    - Reports simulator state metrics (collision, progress, etc.)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique skill identifier."""

    @abstractmethod
    def reset(self, params: Dict[str, Any]) -> None:
        """Initialize skill with parameters from action event."""

    @abstractmethod
    def step(self, state: Dict[str, Any]) -> SkillResult:
        """Compute next action given current simulator state.

        Args:
            state: Current robot state including:
                - position: (x, y) current position
                - heading: float current heading in radians
                - obstacles: list of (x, y) obstacle positions
                - collision: bool whether collision detected

        Returns:
            SkillResult with action command and metrics
        """

    @abstractmethod
    def is_complete(self) -> bool:
        """Check if skill has completed its objective."""

    def get_metrics(self) -> Dict[str, Any]:
        """Return current skill metrics for reporting."""
        return {}
