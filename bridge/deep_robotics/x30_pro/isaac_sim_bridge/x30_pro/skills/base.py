"""Base interface for policy-driven robot skills."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class SkillResult:
    action: str
    speed: float = 0.0
    angular_speed: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    status: str = "running"


class RobotSkill(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    @abstractmethod
    def reset(self, params: Dict[str, Any]) -> None: ...
    @abstractmethod
    def step(self, state: Dict[str, Any]) -> SkillResult: ...
    @abstractmethod
    def is_complete(self) -> bool: ...
