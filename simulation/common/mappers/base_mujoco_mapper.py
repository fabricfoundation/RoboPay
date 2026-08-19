"""Base MuJoCo CommandMapper — translates ActionEvents to actuator targets.

Unlike the ROS2 bridge which maps to Twist (geometry_msgs), MuJoCo bridge
maps to actuator control arrays (data.ctrl[]).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ActuatorCommand:
    """Target values for MuJoCo actuators."""
    ctrl: List[float]          # data.ctrl[] values
    duration_sec: float = 0.0  # how long to hold (0 = one step)
    skill: str = ""            # skill being executed
    metrics_fn: Optional[Any] = None  # callable to extract metrics


class MuJoCoCommandMapper(ABC):
    """Base class for robot-specific MuJoCo actuator mapping.

    Subclass this for each robot model. The mapper translates a Fabric
    ActionEvent into an ActuatorCommand that the bridge applies to
    data.ctrl[] each simulation step.
    """

    def __init__(self, n_actuators: int):
        self.n_actuators = n_actuators

    @abstractmethod
    def map(self, action: str, params: Dict[str, Any]) -> ActuatorCommand:
        """Map an action + params to actuator targets.

        Args:
            action: Skill ID (e.g. "move_forward", "navigate_obstacle")
            params: Action parameters from the ActionEvent

        Returns:
            ActuatorCommand with ctrl values for data.ctrl[]
        """

    def zero(self) -> ActuatorCommand:
        """Return a zero-command (stop)."""
        return ActuatorCommand(ctrl=[0.0] * self.n_actuators, skill="stop")

    def clamp(self, value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))
