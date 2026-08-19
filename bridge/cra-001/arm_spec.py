"""cra-001 --- engine-independent robot spec and skill plan.

Single source of truth shared by every physics backend. Link lengths, the
gripper geometry, the trajectory keyframes, the scene table and the pass/fail
thresholds all live here, so a sim-to-sim comparison is a comparison of two
PHYSICS ENGINES executing one skill definition -- not two hand-written demos
that happen to agree.

Nothing in this module touches a simulator.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- geometry --
BASE_H = 0.40           # shoulder pivot height (m)
LINK1 = 0.28            # upper arm length
LINK2 = 0.24            # forearm length
MAX_REACH = LINK1 + LINK2
GRIP_MID = 0.065        # wrist origin -> finger pad midpoint
FINGER_HALF_X = 0.014
FINGER_HALF_Z = 0.045
PAD_HALF = 0.008        # finger pad half-thickness
PAD_SQUEEZE = 0.0008    # pad penetration at full closure -> sane contact force

CUBE_HALF = 0.025
CUBE_MASS = 0.10
CUBE_FRICTION = 1.6

FINGER_OPEN = 0.050                                   # half-aperture, clear
FINGER_CLOSED = CUBE_HALF + PAD_HALF - PAD_SQUEEZE    # half-aperture, gripping

TIMESTEP = 0.002

ARM_JOINTS = ("pan", "shoulder", "elbow", "wristp")

# --------------------------------------------------------- trajectory plan --
STAGE_STEPS = {
    "move_above": 70,
    "descend": 50,
    "grip": 60,
    "lift": 60,
    "settle": 20,
}
NOMINAL_STEPS = sum(STAGE_STEPS.values())
DEFAULT_BUDGET = 500            # covers the full 7-phase pick+stack (~460 steps); only the `timeout` scenario clips it

# --------------------------------------------------------------- decisions --
GRASP_FORCE_MIN = 0.30          # N, minimum measured normal force to hold
LIFT_MIN = 0.030                # m, minimum vertical displacement for success
UNREACHABLE_GAP = 0.120         # m, tip-to-object residual that means "no"

# ------------------------------------------------------------- scene table --
# Every named object is a physical scene, not a code branch.
WORK_R = 0.35
SCENES = {
    "cube": {"cube": (0.35, 0.0), "obstacle": None, "budget": DEFAULT_BUDGET},
    "unreachable": {"cube": (0.95, 0.0), "obstacle": None, "budget": DEFAULT_BUDGET},
    "collision": {"cube": (0.35, 0.0), "obstacle": (0.27, 0.0), "budget": DEFAULT_BUDGET},
    "timeout": {"cube": (0.35, 0.0), "obstacle": None, "budget": 60},
}
ALIASES = {
    "far_cube": "unreachable",
    "blocked_cube": "collision",
    "slow_cube": "timeout",
}
OBSTACLE_RADIUS = 0.035
OBSTACLE_HALF_H = 0.14


def resolve_scene(params: dict | None):
    """(display_name, scene_key, scene_dict) for a skill parameter block."""
    params = params or {}
    name = str(params.get("object", "cube"))
    key = ALIASES.get(name, name)
    if key not in SCENES:
        key = "cube"
    scene = dict(SCENES[key])
    if "maxSteps" in params:
        scene["budget"] = int(params["maxSteps"])
    return name, key, scene


# ------------------------------------------------------ closed-form keyframes
def solve(r: float, wrist_z: float, pan: float = 0.0):
    """Closed-form 2-link placement of the wrist at (r, wrist_z).

    Used only while building the keyframe table below -- never on the hot
    path. Both engines drive hinge joints about +Y, so rotating body +X by t
    maps it to (cos t, 0, -sin t); substituting phi = -shoulder and
    psi = -(shoulder + elbow) turns the chain into the textbook planar form
    and the solution is a single acos. Wrist pitch is picked so the
    accumulated pitch is zero, keeping the gripper pointed straight down.
    """
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


def forward(pose: dict) -> tuple:
    """Wrist position in world frame -- self-checks and diagnostics."""
    a = pose["shoulder"]
    c = a + pose["elbow"]
    r = LINK1 * math.cos(a) + LINK2 * math.cos(c)
    z = BASE_H - LINK1 * math.sin(a) - LINK2 * math.sin(c)
    return r * math.cos(pose["pan"]), r * math.sin(pose["pan"]), z


GRASP_WZ = CUBE_HALF + GRIP_MID          # 0.090 -- pads straddle the cube CoM
KEYFRAMES = {
    "home": solve(0.20, 0.42),
    "above": solve(WORK_R, GRASP_WZ + 0.14),
    "grasp": solve(WORK_R, GRASP_WZ),
    "lift": solve(WORK_R, GRASP_WZ + 0.15),
    # full stretch: physically demonstrates an out-of-envelope target
    "stretch": {"pan": 0.0, "shoulder": 0.0, "elbow": 0.0, "wristp": 0.0},
}
for _k, _v in KEYFRAMES.items():
    if _v is None:                                    # pragma: no cover
        raise RuntimeError(f"keyframe {_k} is not solvable -- check link geometry")


def smoothstep(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


def blend(p0: dict, p1: dict, u: float) -> dict:
    s = smoothstep(u)
    return {k: p0[k] + (p1[k] - p0[k]) * s for k in ARM_JOINTS}


def aperture_at(u: float) -> float:
    """Commanded half-aperture during the GRIP stage."""
    return FINGER_OPEN + (FINGER_CLOSED - FINGER_OPEN) * smoothstep(u)


# ------------------------------------------------------------------ result --
class PickResult:
    def __init__(self, success: bool, reason: str, metrics: dict):
        self.success = success
        self.reason = reason
        self.metrics = metrics

    def to_dict(self) -> dict:
        return {"success": self.success, "reason": self.reason,
                "metrics": self.metrics}

    def __repr__(self) -> str:                        # pragma: no cover
        return f"PickResult({self.success}, {self.reason!r}, {self.metrics})"


class BudgetExhausted(Exception):
    """Raised when the hard step budget runs out mid-trajectory."""


def build_metrics(*, engine, obj, scene_key, stage, grasp_state,
                  start_pos, end_pos, hold_force, peak_force, contact_samples,
                  collisions, steps, budget, wall_time, note,
                  extra=None) -> dict:
    """Identical metric schema for every backend."""
    delta = [round(end_pos[i] - start_pos[i], 4) + 0.0 for i in range(3)]
    record = {
        "robotId": "cra-001",
        "skillId": "pick_and_stack",
        "engine": engine,
        "object": obj,
        "scene": scene_key,
        "stage": stage,
        "graspState": grasp_state,
        "objectStart": [round(v, 4) + 0.0 for v in start_pos],
        "objectEnd": [round(v, 4) + 0.0 for v in end_pos],
        "objectDelta": delta,
        "objectLifted": delta[2],
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
    # Skill-specific success fields promoted to the top level so the
    # successEvidence clauses in execution-mapping.yaml resolve against real
    # metric keys instead of being buried in the free-text note.
    if extra:
        record.update(extra)
    return record
