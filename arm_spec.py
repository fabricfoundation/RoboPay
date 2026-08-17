"""door-arm-001 --- engine-independent robot spec for door-opening skill.

Single source of truth shared by every physics backend. Door geometry, handle
position, swing arc, and the pass/fail thresholds all live here.

Nothing in this module touches a simulator.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- geometry --
BASE_H = 0.80           # shoulder pivot height (m) - standing robot
LINK1 = 0.28            # upper arm length
LINK2 = 0.24            # forearm length
MAX_REACH = LINK1 + LINK2

# Door parameters
DOOR_WIDTH = 0.50       # m, narrow door for simulation reachability
DOOR_HEIGHT = 2.10      # m
DOOR_THICKNESS = 0.04   # m
DOOR_HANDLE_HEIGHT = 0.85  # m, handle center height (reachable with BASE_H=0.80)
DOOR_HANDLE_RADIUS = 0.04  # m

# Arm end-effector parameters
GRIP_MID = 0.065        # wrist origin -> finger pad midpoint
FINGER_HALF_X = 0.014
FINGER_HALF_Z = 0.045
PAD_HALF = 0.008

TIMESTEP = 0.002

ARM_JOINTS = ("pan", "shoulder", "elbow", "wristp")

# --------------------------------------------------------- trajectory plan --
STAGE_STEPS = {
    "move_above": 70,
    "descend": 50,
    "grip": 80,
    "pull": 100,
    "settle": 30,
}
NOMINAL_STEPS = sum(STAGE_STEPS.values())
DEFAULT_BUDGET = 400

# --------------------------------------------------------------- decisions --
GRASP_FORCE_MIN = 0.25          # N
PULL_MIN = 0.40                 # m
OPEN_ANGLE_MIN = 0.5            # radians (~29 degrees)
UNREACHABLE_GAP = 0.100         # m

# ------------------------------------------------------------- scene table --
WORK_R = 0.35
SCENES = {
    "open": {
        "door_x": 0.0,
        "door_y": 0.0,
        "friction": 0.3,
        "budget": DEFAULT_BUDGET,
    },
    "stuck": {
        "door_x": 0.0,
        "door_y": 0.0,
        "friction": 5.0,
        "budget": DEFAULT_BUDGET,
    },
    "out_of_range": {
        "door_x": 0.35,
        "door_y": 0.0,
        "friction": 0.3,
        "budget": DEFAULT_BUDGET,
    },
}
ALIASES = {
    "normal": "open",
    "stuck_door": "stuck",
    "far_door": "out_of_range",
}


def resolve_scene(params: dict | None):
    params = params or {}
    name = str(params.get("door", "open"))
    key = ALIASES.get(name, name)
    if key not in SCENES:
        key = "open"
    scene = dict(SCENES[key])
    if "maxSteps" in params:
        scene["budget"] = int(params["maxSteps"])
    return name, key, scene


def solve(r: float, wrist_z: float, pan: float = 0.0):
    h = wrist_z - BASE_H
    d2 = r * r + h * h
    d = math.sqrt(d2)
    if not (abs(LINK1 - LINK2) + 1e-4 < d < MAX_REACH - 1e-4):
        return None
    cos_e = max(-1.0, min(1.0, (d2 - LINK1 ** 2 - LINK2 ** 2) / (2 * LINK1 * LINK2)))
    for sign in (1.0, -1.0):
        e = sign * math.acos(cos_e)
        phi = math.atan2(h, r) - math.atan2(LINK2 * math.sin(e),
                                            LINK1 + LINK2 * math.cos(e))
        psi = phi + e
        shoulder, chain = -phi, -psi
        elbow, wristp = chain - shoulder, -chain
        if abs(shoulder) <= 1.95 and abs(elbow) <= 2.55 and abs(wristp) <= 2.75:
            return {"pan": pan, "shoulder": shoulder,
                    "elbow": elbow, "wristp": wristp}
    return None


def smoothstep(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


def blend(p0: dict, p1: dict, u: float) -> dict:
    s = smoothstep(u)
    return {k: p0[k] + (p1[k] - p0[k]) * s for k in ARM_JOINTS}


def aperture_at(u: float) -> float:
    FINGER_OPEN = 0.050
    FINGER_CLOSED = 0.032
    return FINGER_OPEN + (FINGER_CLOSED - FINGER_OPEN) * smoothstep(u)


# ------------------------------------------------------------------ result --
class DoorResult:
    def __init__(self, success: bool, reason: str, metrics: dict):
        self.success = success
        self.reason = reason
        self.metrics = metrics

    def to_dict(self) -> dict:
        return {"success": self.success, "reason": self.reason,
                "metrics": self.metrics}


class BudgetExhausted(Exception):
    pass


def build_metrics(*, engine, obj, scene_key, stage, handle_state,
                  start_pos, end_pos, hold_force, peak_force,
                  contact_samples, collisions, steps, budget,
                  wall_time, door_angle, note) -> dict:
    return {
        "robotId": "door-arm-001",
        "skillId": "open_door",
        "engine": engine,
        "object": obj,
        "scene": scene_key,
        "stage": stage,
        "handleState": handle_state,
        "doorAngle": door_angle,
        "objectStart": [round(v, 4) + 0.0 for v in start_pos],
        "objectEnd": [round(v, 4) + 0.0 for v in end_pos],
        "objectDelta": [round(end_pos[i] - start_pos[i], 4) + 0.0 for i in range(3)],
        "objectLifted": end_pos[2] - start_pos[2],
        "contactForce": round(hold_force or peak_force, 4),
        "peakForce": round(peak_force, 4),
        "contactSamples": contact_samples,
        "collisionCount": collisions,
        "stepsUsed": steps,
        "stepBudget": budget,
        "simTime": round(steps * TIMESTEP, 4),
        "wallTime": round(wall_time, 4),
        "note": note,
    }
