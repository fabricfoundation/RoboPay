"""Real vendor DH spec + skill plan for xarm-real-001 (UFactory).

Single source of truth. DH is the published UFactory table; link
lengths/offsets are real. Shared by MuJoCo + PyBullet backends.
"""
from __future__ import annotations
import math

# (a[m], alpha[deg], d[m], theta_home[deg]) -- UFactory DH
DH = [
    (0.0, -90, 0.267, 0),
    (-0.176, 0, 0.0, 0),
    (-0.176, 0, 0.0, 0),
    (0.0, -90, 0.207, 0),
    (0.0, 90, 0.105, 0),
    (0.0, 0, 0.105, 0),
]
NDOF = 6
LINK_RADII = [0.030 + 0.004 * (6 - i) for i in range(6)]
JOINT_RANGES = [
    (-3.142, 3.142),
    (-3.142, 3.142),
    (-3.142, 3.142),
    (-3.142, 3.142),
    (-3.142, 3.142),
    (-3.142, 3.142)
]
HOME = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

CUBE_HALF = 0.025
CUBE_Z = 0.43          # m, work-surface height the real desktop arm can reach
CUBE_MASS = 0.10
CUBE_FRICTION = 1.6
FINGER_OPEN = 0.050
FINGER_CLOSED = CUBE_HALF + 0.008 - 0.0008
GRASP_FORCE_MIN = 0.30
GRASP_DIST = 0.12          # m, IK residual above this => unreachable
LIFT_MIN = 0.030          # m, min vertical displacement for success
TIMESTEP = 0.002

ROBOT_ID = "xarm-real-001"
SKILL_ID = "push_object"

SCENES = {
    "cube":        {"cube": (0.30, 0.0), "obstacle": None, "budget": 400},
    "unreachable": {"cube": (1.20, 0.0), "obstacle": None, "budget": 400},
    "collision":   {"cube": (0.30, 0.0), "obstacle": (0.18, 0.0), "budget": 400},
    "timeout":     {"cube": (0.30, 0.0), "obstacle": None, "budget": 60},
}
ALIASES = {"far_cube": "unreachable", "blocked_cube": "collision", "slow_cube": "timeout"}


def resolve_scene(params: dict | None):
    params = params or {}
    name = str(params.get("object", "cube"))
    key = ALIASES.get(name, name)
    if key not in SCENES:
        key = "cube"
    scene = dict(SCENES[key])
    if "maxSteps" in params:
        scene["budget"] = int(params["maxSteps"])
    return name, scene


class PickResult:
    def __init__(self, success, reason, metrics):
        self.success = success
        self.reason = reason
        self.metrics = metrics


class BudgetExceeded(Exception):
    pass


def build_metrics(success, residual, lift, steps):
    return {
        "success": success, "residual_m": round(float(residual), 4),
        "lift_m": round(float(lift), 4), "steps": int(steps),
        "robot": ROBOT_ID, "skill": SKILL_ID,
    }
