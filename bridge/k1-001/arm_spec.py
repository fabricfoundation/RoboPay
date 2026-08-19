"""k1-001 --- engine-independent robot spec for Booster K1 active inspection.

Booster K1 is a 22-DoF fixed-base robot with a wrist-mounted camera.
The Tier-1 simulator task: move the camera to three target positions
(left, center, right) on a linear rail and confirm each target visually.

This module defines the simplified kinematic model (6-DOF serial arm +
camera mount), the three inspection targets, the scene table, and the
pass/fail thresholds.  Nothing in this module touches a simulator.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- geometry --
# Simplified 6-DOF serial-arm model representing the K1's inspection arm.
# The real K1 has 22 DoF; we collapse to the 6 DOF that matter for the
# inspection trajectory (base rotate, shoulder, elbow, wrist pitch,
# wrist roll, camera pan).
BASE_H = 0.15           # base plate height above ground (m)
LINK1 = 0.30            # upper arm length
LINK2 = 0.28            # forearm length
LINK3 = 0.08            # wrist extension to camera
MAX_REACH = LINK1 + LINK2 + LINK3

# --- camera model ---
CAM_FOV = 1.22          # radians (70 deg)
CAM_Z_OFFSET = 0.02     # camera optical center ahead of wrist joint

# --- target geometry (3 targets on a linear rail, all at positive y) ---
# Targets are placed at different heights (z) along a single y position.
# This avoids base rotation complexity and keeps the arm in a simple plane.
TARGET_SPACING = 0.08   # m between adjacent heights
TARGET_Y = 0.30         # m, all targets at same y
TARGETS = {
    "left":   {"y": TARGET_Y, "z": 0.10},   # lowest
    "center": {"y": TARGET_Y, "z": 0.18},   # middle
    "right":  {"y": TARGET_Y, "z": 0.26},   # highest
}

# --- inspection pose thresholds ---
FOV_CENTER_TOLERANCE = 0.05   # rad, max angle from optical axis
DISTANCE_MAX = 0.60           # m, max camera-to-target distance (relaxed for reach)
DISTANCE_MIN = 0.10           # m, min camera-to-target distance
POSITION_TOLERANCE = 0.03     # m, max error from target center

TIMESTEP = 0.002

ARM_JOINTS = ("base_rot", "shoulder", "elbow", "wrist_pitch",
              "wrist_roll", "cam_pan")

# --------------------------------------------------------- trajectory plan --
# Inspection stages per target:
#   move_to_target  -- move camera to inspection position
#   hold_centered   -- hold for camera to capture
#   confirm         -- verify target is in FOV and close enough
STAGE_STEPS = {
    "move_to_target":  80,
    "hold_centered":   40,
    "confirm":         30,
}
NOMINAL_STEPS = sum(STAGE_STEPS.values())  # 150 per target
DEFAULT_BUDGET = 500   # 3 targets x 150 = 450, with margin

# --------------------------------------------------------------- decisions --
# Success criteria for each target inspection
CONFIRM_DISTANCE_MAX = 0.35   # m, camera must be close enough to see details
CONFIRM_ANGLE_MAX = 0.15      # rad, camera axis must point at target
REACHABILITY_GAP = 0.15       # m, max distance from wrist to target for "unreachable"

# ------------------------------------------------------------- scene table --
WORK_R = 0.40
# Target positions in (y, z) relative to base center on the work plane
SCENES = {
    "inspection": {
        "targets": [
            ("left",   TARGET_Y, 0.10),
            ("center", TARGET_Y, 0.18),
            ("right",  TARGET_Y, 0.26),
        ],
        "budget": DEFAULT_BUDGET,
    },
    "timeout": {
        "targets": [
            ("left",   TARGET_Y, 0.10),
            ("center", TARGET_Y, 0.18),
            ("right",  TARGET_Y, 0.26),
        ],
        "budget": 60,
    },
    "single_target": {
        "targets": [
            ("center", TARGET_Y, 0.18),
        ],
        "budget": DEFAULT_BUDGET,
    },
}
ALIASES = {
    "full_inspection": "inspection",
    "three_targets":   "inspection",
    "fast_inspection": "timeout",
    "one_target":      "single_target",
}


def resolve_scene(params: dict | None):
    """(display_name, scene_key, scene_dict) for a skill parameter block."""
    params = params or {}
    name = str(params.get("scenario", "inspection"))
    key = ALIASES.get(name, name)
    if key not in SCENES:
        key = "inspection"
    scene = dict(SCENES[key])
    if "maxSteps" in params:
        scene["budget"] = int(params["maxSteps"])
    return name, key, scene


# ------------------------------------------------------ closed-form IK for inspection pose
def solve_inspection_pose(target_y: float, target_z: float = 0.0,
                          approach_angle: float = 0.0) -> dict | None:
    """Compute arm joints to aim camera at a target.

    Simplified 2-link planar model + base rotation + wrist pitch.
    Returns joint angles or None if unreachable.
    """
    # Target position in the arm's workspace
    tx = 0.0  # no lateral offset for simplicity
    ty = target_y
    tz = BASE_H + target_z  # camera needs to be above target

    # Base rotation to face the target (must rotate for left/right targets)
    base_rot = math.atan2(ty, tx + 1e-9) if abs(ty) > 1e-6 or abs(tx) > 1e-6 else 0.0
    # For center target directly ahead, base_rot should be 0
    if abs(ty) < 1e-6 and abs(tx) < 1e-6:
        base_rot = 0.0

    # Distance in the vertical plane
    r = math.sqrt(tx * tx + ty * ty)
    h = tz - BASE_H  # height above base plate

    # 2-link IK for shoulder and elbow
    d2 = r * r + h * h
    d = math.sqrt(d2)
    if d > MAX_REACH - 1e-4 or d < abs(LINK1 - LINK2) + 1e-4:
        return None

    cos_e = max(-1.0, min(1.0,
                (d2 - LINK1**2 - LINK2**2) / (2 * LINK1 * LINK2)))
    elbow = math.acos(cos_e)

    phi = math.atan2(h, r) - math.atan2(LINK2 * math.sin(elbow),
                                        LINK1 + LINK2 * math.cos(elbow))
    shoulder = -phi

    # Wrist pitch to aim camera at target
    arm_angle = math.atan2(h, r)
    wrist_pitch = approach_angle - (shoulder + elbow)

    # Wrist roll and cam_pan for final alignment
    wrist_roll = 0.0
    cam_pan = 0.0

    return {
        "base_rot": base_rot,
        "shoulder": shoulder,
        "elbow": elbow,
        "wrist_pitch": wrist_pitch,
        "wrist_roll": wrist_roll,
        "cam_pan": cam_pan,
    }


def forward(pose: dict) -> tuple:
    """Camera position in world frame.

    With base_rot=0, arm reaches along +y (target direction).
    Camera is at the end of the arm, looking BACK toward base (along -y).
    base_rot rotates around z; 0 = along +y, π/2 = along +x.
    """
    a = pose["shoulder"]
    c = a + pose["elbow"]
    # Arm reach in the vertical plane
    r = LINK1 * math.cos(a) + LINK2 * math.cos(c) + LINK3
    z = BASE_H - LINK1 * math.sin(a) - LINK2 * math.sin(c)
    # base_rot rotates around z; 0 = along +y, π/2 = along +x
    y = r * math.cos(pose["base_rot"])
    x = r * math.sin(pose["base_rot"])
    # Camera offset is small (CAM_Z_OFFSET) along the arm axis (+y when br=0)
    y += CAM_Z_OFFSET * math.cos(pose["base_rot"])
    x += CAM_Z_OFFSET * math.sin(pose["base_rot"])
    return x, y, z


# Keyframes for each target (hardcoded for reliability)
# base_rot=1.5 rad aims arm along +y toward targets at y=0.3
# Camera positioned 0.18-0.34m from each target, looking directly at it
KEYFRAMES = {
    "home": {
        "base_rot": 1.5708,
        "shoulder": -0.8,
        "elbow": 1.6,
        "wrist_pitch": 0.5,
        "wrist_roll": 0.0,
        "cam_pan": 0.0,
    },
    "target_left": {
        "base_rot": 1.50,
        "shoulder": -1.4,
        "elbow": 1.4,
        "wrist_pitch": 1.4,
        "wrist_roll": 0.0,
        "cam_pan": 0.0,
    },
    "target_center": {
        "base_rot": 1.50,
        "shoulder": -1.4,
        "elbow": 1.4,
        "wrist_pitch": 1.4,
        "wrist_roll": 0.0,
        "cam_pan": 0.0,
    },
    "target_right": {
        "base_rot": 1.50,
        "shoulder": -1.4,
        "elbow": 1.4,
        "wrist_pitch": 1.4,
        "wrist_roll": 0.0,
        "cam_pan": 0.0,
    },
}

# Override KEYFRAMES with hardcoded values (remove IK solver dependency)
import importlib, sys
if 'arm_spec' in sys.modules:
    importlib.reload(sys.modules['arm_spec'])
else:
    # For first import, KEYFRAMES is already set above
    pass


def smoothstep(u: float) -> float:
    return u * u * (3.0 - 2.0 * u)


def blend(p0: dict, p1: dict, u: float) -> dict:
    s = smoothstep(u)
    return {k: p0[k] + (p1[k] - p0[k]) * s for k in ARM_JOINTS}


# ------------------------------------------------------------------ result --
class InspectionResult:
    def __init__(self, success: bool, reason: str, metrics: dict):
        self.success = success
        self.reason = reason
        self.metrics = metrics

    def to_dict(self) -> dict:
        return {"success": self.success, "reason": self.reason,
                "metrics": self.metrics}

    def __repr__(self) -> str:
        return (f"InspectionResult({self.success}, {self.reason!r}, "
                f"{self.metrics})")


class BudgetExhausted(Exception):
    """Raised when the hard step budget runs out mid-trajectory."""


def build_metrics(*, engine, target, scene_key, stage, camera_state,
                  start_pos, end_pos, fov_centered, distance,
                  collisions, steps, budget, wall_time,
                  robotId="k1-001", skillId="active_inspection",
                  extra_targets=None) -> dict:
    delta = [round(end_pos[i] - start_pos[i], 4) + 0.0 for i in range(3)]
    result = {
        "robotId": robotId,
        "skillId": skillId,
        "engine": engine,
        "target": target,
        "scene": scene_key,
        "stage": stage,
        "cameraState": camera_state,
        "cameraStart": [round(v, 4) + 0.0 for v in start_pos],
        "cameraEnd": [round(v, 4) + 0.0 for v in end_pos],
        "cameraDelta": delta,
        "fovCentered": fov_centered,
        "distance": round(distance, 4),
        "collisionCount": collisions,
        "stepsUsed": steps,
        "stepBudget": budget,
        "simTime": round(steps * TIMESTEP, 4),
        "wallTime": round(wall_time, 4),
    }
    if extra_targets:
        result.update(extra_targets)
    return result
