"""unitree-g1 --- engine-independent robot spec and skill plan (planar biped).

Single source of truth shared by every physics backend (MuJoCo + PyBullet).

G1 here is modelled as a *planar* biped: a rigid torso that slides in X (forward)
and Z (up) only -- it cannot pitch -- driven by two 2-link legs (hip + knee
hinges). Four actuated joints, four PD actuators. Locomotion is a deterministic,
open-loop stepping gait: one foot is planted (high friction) while the other
swings forward and plants ahead, ratcheting the torso forward. The gait is the
same for every engine, so MuJoCo and PyBullet must agree -- that is what
``test_sim2sim`` checks.

For the ``balance_recover`` skill the torso also carries a *pitch* degree of
freedom about the hip line (a real inverted-pendulum fall axis). A disturbance
pushes the torso; a *torque-limited* balance PD controller (exactly the kind a
real actuator has) tries to hold it upright. A gentle push stays within the
actuator's torque authority and is caught (the robot recovers -> success ->
payment). A hard push exceeds that authority, the torso tips past
``FALL_PITCH`` and the robot falls (genuine physics failure -> no settlement).
Both backends implement the *same* joint, the *same* gains and the *same* torque
cap, so the recover/fall verdict is a property of the definition, not of one
solver.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- geometry --
# Link lengths (metres). The planar biped stands with both feet on the ground.
TORSO_H = 0.55            # torso box height (m)
THIGH_LEN = 0.31          # thigh link length (m)
SHANK_LEN = 0.31          # shank link length (m)
FOOT_HALF = 0.06          # foot half-length (m)
FOOT_H = 0.03             # foot height (m)
HIP_X_OFFSET = 0.09       # lateral (Y) offset of each hip from the sagittal plane

# Standing hip height: hip joint sits THIGH+SHANK below the foot contact.
HIP_Z = THIGH_LEN + SHANK_LEN + FOOT_H          # = 0.65 m
# Torso centre height when standing straight (hip at bottom of torso box).
STAND_Z = HIP_Z + TORSO_H / 2.0                 # = 0.925 m

# The four actuated leg joints, in actuator order.
LEG_JOINTS = ("left_hip", "left_knee", "right_hip", "right_knee")
LEFT = ("left_hip", "left_knee")
RIGHT = ("right_hip", "right_knee")

# Joint limits (radians). Hip: +/- swing. Knee: always bends positive (never hyperextends).
HIP_MIN, HIP_MAX = -1.3, 1.3
KNEE_MIN, KNEE_MAX = 0.0, 2.4

# --------------------------------------------------------- gait constants --
STEP_LEN = 0.18          # forward distance advanced per footfall (m)
STEP_CLEAR = 0.12        # swing-foot clearance above the ground (m)
SWING_STEPS = 25         # control steps for one swing phase (half a stride)
TIMESTEP = 0.004         # physics timestep (s), shared by both engines
WALK_VEL = 0.55          # nominal forward speed used by the demo table (m/s)

# Per-stage control-step budgets used by the staged demo runner.
STAGE_STEPS = {"init": 20, "balance_recover": 250, "stop": 25}
DEFAULT_BUDGET = 1000    # hard cap on control steps for a single skill run

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

# --------------------------------------------------------- balance skills --
# The torso is an inverted pendulum about the hip line. A disturbance injects an
# angular velocity (rad/s); a torque-limited PD (KP_BAL/KV_BAL, capped at
# MAX_TORQUE_BAL) fights to keep the torso upright. Recover vs fall is decided by
# whether the pitch ever exceeds FALL_PITCH.
PUSH_T = 0.30            # fraction of the budget at which the push is applied
PUSH_W_RECOVER = 1.3     # gentle push the torque-limited PD catches -> recovers
PUSH_W_FALL = 8.0        # hard push that exceeds actuator authority -> falls
FALL_PITCH = 0.50        # |pitch| (rad) beyond this = the robot has fallen
RECOVER_PITCH = 0.15     # |pitch| at the end under this = upright again (success)
BALANCE_BUDGET = 250     # control steps for one balance attempt (~1.0 s @ 4 ms)
KP_BAL = 60.0            # balance PD proportional gain (N·m/rad)
KV_BAL = 16.0            # balance PD derivative gain (N·m·s/rad)
# Torque authority is deliberately LOWER than the peak gravity torque on the
# inverted pendulum (m*g*r*sin(theta) ~= 13.5 N·m at theta=90deg). A gentle push
# stays where the capped PD can hold it (recover); a hard push drives the torso
# past the angle where gravity exceeds the cap, so it runs away and falls. That
# is the genuine physics failure path -- not a scripted flag.
MAX_TORQUE_BAL = 6.0     # actuator torque authority (N·m) -- the recover/fall gate

# ------------------------------------------------------------- scene table --
# Each scene is a deterministic target. ``budget`` is the hard step cap; the
# robot succeeds when it stays upright through the disturbance, else it falls.
SCENES = {
    "balance_recover": {
        "durationSec": 1.0,
        "push": PUSH_W_RECOVER,
        "budget": BALANCE_BUDGET,
    },
    "stop": {
        "durationSec": 0.0,
        "push": 0.0,
        "budget": 50,
    },
}
ALIASES = {
    "recover": "balance_recover",
    "balance": "balance_recover",
    "stand": "stop",
}


def resolve_scene(params: dict | None = None, skill: str | None = None):
    """Return (display_name, scene_key, scene_dict) for a skill parameter block.

    ``skill`` (the resolved skill id from the request) takes priority over any
    ``skill``/``object`` key inside ``params``. Unknown names fall back to
    ``balance_recover``. Numeric overrides (push) are applied on top of the base
    scene.
    """
    params = params or {}
    name = str(skill if skill is not None
               else params.get("skill", params.get("object", "balance_recover")))
    key = ALIASES.get(name, name)
    if key not in SCENES:
        key = "balance_recover"
    scene = dict(SCENES[key])
    if "push" in params:
        scene["push"] = float(params["push"])
    return name, key, scene


def leg_ik(dx: float, dz: float):
    """2-link inverse kinematics for one leg (thigh + shank).

    ``dx`` is the foot target's horizontal offset forward of the hip (m);
    ``dz`` is the foot target's vertical offset below the hip (m, positive
    downward). Returns the hip and knee joint angles (radians) in the model's
    convention: hip=0 means the thigh points straight down; a *negative* hip
    tilts the foot forward (+X); the knee only ever bends positive (never
    hyperextends), which is the natural human-like bend for a foot below the
    hip.

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
    skill_id = scene_key if scene_key in SCENES else "balance_recover"
    distance = round(math.hypot(delta[0], delta[1]), 4)
    return {
        "robotId": "unitree-g1",
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
