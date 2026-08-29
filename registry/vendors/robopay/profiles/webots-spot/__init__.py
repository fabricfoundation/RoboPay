"""RoboPay Webots Spot (Tier 1) Profile Package."""

__version__ = "1.0.0"
__profile_id__ = "robopay-webots-spot-tier1"

from pathlib import Path

PROFILE_DIR = Path(__file__).parent
PROFILE_CONFIG = PROFILE_DIR / "profile.json"
PROFILE_README = PROFILE_DIR / "README.md"


def get_profile_metadata() -> dict:
    """Load and return profile metadata."""
    import json
    return json.loads(PROFILE_CONFIG.read_text())


__all__ = ["__version__", "__profile_id__", "PROFILE_DIR", "get_profile_metadata"]
