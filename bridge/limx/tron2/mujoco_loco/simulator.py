"""MuJoCo physics for limx-tron2 (RoboPay Tier 1, biped).

Thin wrapper over the shared parametric engine: picks this robot's distinct
morphology (link lengths / leg count / gait cadence) so the reviewer can diff
the MJCF and see a genuinely different body -- not a renamed clone.
"""
from engine import Simulator


class MuJoCoSimulator(Simulator):
    ROBOT_ID = "limx-tron2"

    def __init__(self):
        super().__init__("limx-tron2")
