"""tron1-001 --- engine-independent robot spec and skill plan (quadruped).

Single source of truth shared by every physics backend (MuJoCo + PyBullet).

TRON1 is modelled after the LimX Dynamics TRON1 compact quadruped: a rigid
torso that slides in X (forward) and Z (up), pitched by the four legs, driven
by eight hinge joints (hip + knee per leg). Locomotion is a deterministic,
open-loop *trot* gait: the two diagonal leg pairs (FL+RR and FR+RL) swing and
plant alternately, so two feet are always on the ground, ratcheting the torso
forward. The gait is the same for every engine, so MuJoCo and PyBullet must
agree -- that is what ``test_sim2sim`` checks.

Geometry is constrained by the published TRON1 envelope (compact ~0.35 m
standing height, ~0.55 m body length); the model itself is a documented,
simplified planar quadruped that reproduces that envelope and the trot gait
under real gravity -- not a claimed reproduction of an unpublished CAD model.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- geometry --
# Link lengths (metres). The quadruped stands with all four feet on the ground.
TORSO_H = 0.14            # torso box height (m)
TORSO_L = 0.50            # torso box length along X (m)
THIGH_LEN = 0.14          # thigh link length (m)
SHANK_LEN = 0.16          # shank link length (m)
FOOT_HALF = 0.03          # foot half-length (m)
FOOT_H = 0.02             # foot height (m)
HIP_X_OFFSET = 0.16       # hip longitudinal (X) offset from the torso centre (m)

# Standing hip height: hip joint sits THIGH+SHANK below the foot contact.
HIP_Z = THIGH_LEN + SHANK_LEN + FOOT_H          # = 0.32 m (compact TRON1 stance)
# Torso centre height when standing straight (hip at bottom of torso box).
STAND_Z = HIP_Z + TORSO_H / 2.0                 # = 0.39 m

# The eight actuated joints, in actuator order. Diagonal pairs trot together:
#   diagonal pair A = (fl_hip, fl_knee, rr_hip, rr_knee)
#   diagonal pair B = (fr_hip, fr_knee, rl_hip, rl_knee)
LEG_JOINTS = (
    "fl_hip", "fl_knee",
    "fr_hip", "fr_knee",
    "rl_hip", "rl_knee",
    "rr_hip", "rr_knee",
)
DIAG_A = ("fl_hip", "fl_knee", "rr_hip", "rr_knee")
DIAG_B = ("fr_hip", "fr_knee", "rl_hip", "rl_knee")
FRONT = ("fl_hip", "fl_knee", "fr_hip", "fr_knee")
REAR = ("rl_hip", "rl_knee", "rr_hip", "rr_knee")

# Joint limits (radians). Hip: +/- swing. Knee: always bends positive (never hyperextends).
HIP_MIN, HIP_MAX = -1.3, 1.3
KNEE_MIN, KNEE_MAX = 0.0, 2.4

# --------------------------------------------------------- gait constants --
STEP_LEN = 0.16          # forward distance advanced per footfall pair (m)
STEP_CLEAR = 0.10        # swing-foot clearance above the ground (m)
SWING_STEPS = 25         # control steps for one diagonal-pair swing phase
TIMESTEP = 0.004         # physics timestep (s), shared by both engines
WALK_VEL = 0.50          # nominal forward speed used by the demo table (m/s)

# Per-stage control-step budgets used by the staged demo runner.
STAGE_STEPS = {"init": 20, "move_forward": 220, "stop": 25}
DEFAULT_BUDGET = 1200    # hard cap on control steps for a single skill run

# --------------------------------------------------------- skill params ---
WALK_SPEED_MIN = 0.0
WALK_SPEED_MAX = 1.5
WALK_SPEED_DEFAULT = 0.6
GOAL_DIST = 1.0          # default goal distance for move_forward (m)
GOAL_THRESHOLD = 0.3     # distance to target at which a goal counts as reached (m)

# Obstacle (a low curb the walker must step over).
OBSTACLE_HALF_X = 0.05   # curb half-width along X (m) -> 0.10 m wide
OBSTACLE_HALF_Z = 0.04   # curb half-height (m) -> top at 0.04 m
OBSTACLE_CLEAR_Z = 0.07  # foot must clear this height when crossing (m)

# ------------------------------------------------------------- scene table --
# Each scene is a deterministic target. ``budget`` is the hard step cap; the
# walker succeeds when it reaches the goal within the budget, else times out.
SCENES = {
    "move_forward": {
        "durationSec": 3.0,
        "speed": WALK_SPEED_DEFAULT,
        "obstacles": [],
        "goalDist": GOAL_DIST,
        "budget": DEFAULT_BUDGET,
    },
    "navigate_obstacle": {
        "goal_x": 2.0,
        "goal_y": 0.0,
        "obstacles": [(1.0, OBSTACLE_HALF_Z)],
        "goalDist": GOAL_DIST,
        "budget": DEFAULT_BUDGET,
    },
    "stop": {
        "durationSec": 0.0,
        "speed": 0.0,
        "obstacles": [],
        "budget": 50,
    },
}
ALIASES = {
    "forward": "move_forward",
    "walk": "move_forward",
    "obstacle": "navigate_obstacle",
    "nav": "navigate_obstacle",
}


def resolve_scene(params: dict | None = None, skill: str | None = None):
    """Return (display_name, scene_key, scene_dict) for a skill parameter block.

    ``skill`` (the resolved skill id from the request) takes priority over any
    ``skill``/``object`` key inside ``params``. Unknown names fall back to
    ``move_forward``. Numeric overrides (durationSec / speed / goal_x / goal_y /
    goalDistance) are applied on top of the base scene.
    """
    params = params or {}
    name = str(skill if skill is not None
               else params.get("skill", params.get("object", "move_forward")))
    key = ALIASES.get(name, name)
    if key not in SCENES:
        key = "move_forward"
    scene = dict(SCENES[key])
    if "durationSec" in params:
        scene["durationSec"] = float(params["durationSec"])
    if "speed" in params:
        scene["speed"] = float(params["speed"])
    if "goalDistance" in params:
        scene["goalDist"] = float(params["goalDistance"])
    elif "goalDist" in params:
        scene["goalDist"] = float(params["goalDist"])
    if "goal_x" in params:
        scene["goal_x"] = float(params["goal_x"])
    if "goal_y" in params:
        scene["goal_y"] = float(params["goal_y"])
    return name, key, scene


def leg_ik(dx: float, dz: float):
    """2-link inverse kinematics for one leg (thigh + shank).

    ``dx`` is the foot target's horizontal offset forward of the hip (m);
    ``dz`` is the foot target's vertical offset below the hip (m, positive
    downward). Returns the hip and knee joint angles (radians) in the model's
    convention: hip=0 means the thigh points straight down; a *negative* hip
    tilts the foot forward (+X); the knee only ever bends positive (never
    hyperextends), which is the natural bend for a foot below the hip.

    Derived from the model's forward kinematics:
        foot_x = -L1*sin(h) - L2*sin(h+k)
        foot_z = -L1*cos(h) - L2*cos(h+k)      (relative to the hip, down = -Z)
    """
    l1, l2 = THIGH_LEN, SHANK_LEN
    # Work in (forward, down) with down positive.
    xf = float(dx)
    zd = -float(dz)                       # dz<0 (below hip) -> zd>0
    r = math.hypot(xf, zd)
    r = min(max(r, abs(l1 - l2) + 1e-4), l1 + l2 - 1e-4)
    # Rescale (xf, zd) to the clamped reach, preserving direction.
    if math.hypot(xf, zd) > 0:
        xf = xf / math.hypot(xf, zd) * r
        zd = zd / math.hypot(xf, zd) * r
    # Angle of the line hip->foot from straight-down (positive = forward).
    phi = math.atan2(xf, zd)
    # Interior angle at the hip between the thigh and the line hip->foot.
    cos_a = (l1 * l1 + r * r - l2 * l2) / (2.0 * l1 * r)
    cos_a = min(max(cos_a, -1.0), 1.0)
    a = math.acos(cos_a)
    # The thigh points further forward than the line hip->foot (knee tucks the
    # shank back), so the thigh's forward tilt is phi + a.
    thigh_fwd = phi + a
    # Model sign: positive hip joint angle tilts the foot backward, so a
    # forward thigh needs a negative joint angle.
    hip = -thigh_fwd
    # Knee bend: interior angle at the knee, joint = pi - interior (0 = straight).
    cos_int = (l1 * l1 + l2 * l2 - r * r) / (2.0 * l1 * l2)
    cos_int = min(max(cos_int, -1.0), 1.0)
    knee = math.pi - math.acos(cos_int)
    # Clamp to joint limits.
    hip = min(max(hip, HIP_MIN), HIP_MAX)
    knee = min(max(knee, KNEE_MIN), KNEE_MAX)
    return hip, knee


def trot_foot_targets(torso_x: float, step_idx: int, swing_phase: bool,
                      obstacles) -> dict:
    """Closed-form foot placements for the trot gait.

    ``torso_x`` is the torso centre X. Diagonal pair A (FL+RR) swings on odd
    half-strides, pair B (FR+RL) on even ones. The planted feet stay under the
    torso; the swinging feet advance by ``STEP_LEN`` and lift by
    ``STEP_CLEAR`` (or ``OBSTACLE_CLEAR_Z`` over a curb). Returns a dict
    mapping joint name -> (hip_angle, knee_angle) for all eight joints.
    """
    targets = {}
    # Longitudinal hip positions relative to the torso centre.
    hips = {
        "fl": (HIP_X_OFFSET, 0.0), "fr": (HIP_X_OFFSET, 0.0),
        "rl": (-HIP_X_OFFSET, 0.0), "rr": (-HIP_X_OFFSET, 0.0),
    }
    pair_a_swings = (step_idx % 2) == 0
    for leg, (hx, _hy) in hips.items():
        hip_world_x = torso_x + hx
        ground = _ground_z(hip_world_x, obstacles)
        swing = ((leg in ("fl", "rr") and pair_a_swings) or
                 (leg in ("fr", "rl") and not pair_a_swings))
        if swing:
            foot_x = hip_world_x + STEP_LEN
            clear = max(STEP_CLEAR, OBSTACLE_CLEAR_Z)
            foot_z = ground + clear
        else:
            foot_x = hip_world_x
            foot_z = ground
        dx = foot_x - hip_world_x
        dz = -(HIP_Z - foot_z)          # down from hip to foot
        hip_ang, knee_ang = leg_ik(dx, dz)
        targets[f"{leg}_hip"] = (hip_ang, knee_ang)
    return targets


def _ground_z(x: float, obstacles) -> float:
    """Surface height under a foot at world X (0 on flat ground, curb top on a
    curb). ``obstacles`` is a list of (center_x, half_z) curbs."""
    z = 0.0
    for (cx, hz) in (obstacles or ()):
        if abs(x - cx) <= OBSTACLE_HALF_X:
            z = max(z, 2.0 * hz)          # box top = 2 * half-height
    return z


# ------------------------------------------------------------------ result --
class WalkResult:
    def __init__(self, success: bool, message: str, metrics: dict):
        self.success = success
        self.message = message
        self.metrics = metrics or {}

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "metrics": self.metrics,
        }

    def __repr__(self) -> str:                        # pragma: no cover
        return f"WalkResult({self.success}, {self.message!r}, {self.metrics})"


class BudgetExhausted(Exception):
    """Raised when the hard step budget runs out before the goal is reached."""


def build_metrics(*, engine: str, scene_key: str, stage: str,
                  start_pos, end_pos, steps: int, budget: int,
                  wall_time: float, note: str) -> dict:
    """Identical metric schema for every backend (reviewer-verifiable)."""
    delta = [round(float(end_pos[i] - start_pos[i]), 4) for i in range(3)]
    skill_id = scene_key if scene_key in SCENES else "move_forward"
    distance = round(math.hypot(delta[0], delta[1]), 4)
    return {
        "robotId": "tron1-001",
        "skillId": skill_id,
        "engine": engine,
        "scene": scene_key,
        "stage": stage,
        "positionStart": [round(float(v), 4) for v in start_pos],
        "positionEnd": [round(float(v), 4) for v in end_pos],
        "positionDelta": delta,
        "distanceTraveled": distance,
        "stepsUsed": int(steps),
        "stepBudget": int(budget),
        "simTime": round(steps * TIMESTEP, 4),
        "wallTime": round(wall_time, 4),
        "note": note,
    }