"""MuJoCo-specific CommandMapper implementations for each robot model.

Each mapper extends the bridge's CommandMapper pattern to translate
ActionEvents into MuJoCo actuator commands.
"""
from .base_mujoco_mapper import MuJoCoCommandMapper

__all__ = ["MuJoCoCommandMapper"]
