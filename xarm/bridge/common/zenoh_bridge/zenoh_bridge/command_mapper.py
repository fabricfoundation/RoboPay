"""Base class for robot-specific action → command mapping."""
from abc import ABC, abstractmethod

from geometry_msgs.msg import Twist

from .action_event import ActionEvent


class CommandMapper(ABC):
    """Subclass this for each robot model."""

    @abstractmethod
    def map(self, event: ActionEvent) -> Twist:
        """Map an ActionEvent to a Twist command."""
