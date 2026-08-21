"""Payment-gated Boston Dynamics Atlas DRC legacy simulation bridge."""

from .contracts import ATLAS_ROBOT_ID, PROFILE_ID, WAVE_SKILL_ID
from .runtime import run_wave_episode

__all__ = ["ATLAS_ROBOT_ID", "PROFILE_ID", "WAVE_SKILL_ID", "run_wave_episode"]
