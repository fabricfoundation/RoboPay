"""DEPRECATED — NOT a real simulator. DO NOT USE for Sim-to-Sim validation.

This file was previously used as a silent fallback when the Webots binary was
not available. It does NOT run any physics engine. It is a kinematic toy:

    qpos += 0.2 * (target - qpos)   # first-order filter, no dynamics

Using this as a "Webots" substitute violates the Sim-to-Sim requirement, which
demands two genuinely different physics engines (e.g., MuJoCo + real Webots).

The correct approach is:
  1. Install Webots R2023b+ (https://cyberbotics.com/)
  2. Set WEBOTS_EXE=/path/to/webots (or add to PATH)
  3. Run sim2sim.py — it will launch the real Webots binary with the
     reachy_mini_controller that imports the same ReachyTaskPolicy.

If Webots is not available, sim2sim.py now FAILS explicitly instead of
silently substituting this mock.
"""

import warnings


class ReachyMiniWebotsEnvironment:
    """DEPRECATED kinematic mock. Raises on instantiation."""

    def __init__(self, *args, **kwargs):
        warnings.warn(
            "ReachyMiniWebotsEnvironment is a DEPRECATED kinematic mock with NO physics. "
            "It must NOT be used for Sim-to-Sim validation. "
            "Install Webots and use the real subprocess path in sim2sim.py instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        raise RuntimeError(
            "ReachyMiniWebotsEnvironment is disabled. "
            "This is NOT a real simulator. Install Webots R2023b+ for genuine "
            "Sim-to-Sim validation. See sim2sim.py for the correct subprocess path."
        )
