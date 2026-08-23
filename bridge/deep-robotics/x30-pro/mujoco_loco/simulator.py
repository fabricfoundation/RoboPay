"""MuJoCo physics for deep-robotics-x30-pro (RoboPay Tier 1, quadruped).

Thin wrapper over the shared parametric engine: picks this robot's distinct
morphology (link lengths / leg count / gait cadence) so the reviewer can diff
the MJCF and see a genuinely different body -- not a renamed clone.
"""
from engine import Simulator


class MuJoCoSimulator(Simulator):
    ROBOT_ID = "deep-robotics-x30-pro"

    def __init__(self):
        super().__init__("deep-robotics-x30-pro")
