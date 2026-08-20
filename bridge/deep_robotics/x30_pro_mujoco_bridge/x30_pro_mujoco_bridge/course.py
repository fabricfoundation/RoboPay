"""Canonical, profile-owned physical course shared by both simulators."""

from __future__ import annotations

from hashlib import sha256
import json
import math
from typing import Any


COURSE_ID = "x30-pro-two-obstacle-slow-slalom-v5"
COURSE_VERSION = 5
# World coordinates are deliberately shared. Both scenes point the official
# model's local +X nose toward global -X. The two physical blockers are
# directly on the initial travel axis. The measured-state controller must
# establish physical side clearance before reaching each obstacle and only
# then cross the finish line.
# (With the nose facing -X, +world-Y is body-right.)
BLOCKER_CENTERS_M = (
    (-1.05, 0.00),
    (-1.00, 0.45),
)
BLOCKER_HALF_EXTENTS_M = (0.015, 0.015, 0.22)
MIN_FORWARD_PROGRESS_M = 0.85
FIRST_CLEARANCE_OFFSET_M = 0.50
SECOND_CLEARANCE_OFFSET_M = 0.55
FIRST_OBSTACLE_PASSED_PROGRESS_M = 0.65
MAX_APPROACH_CLEARANCE_M = 1.80
FINISH_LINE_X_M = -1.00
GOAL_CENTER_M = (-1.05, 0.75)


def spec() -> dict[str, Any]:
    """Return a JSON-serialisable course contract, excluding its fingerprint."""

    return {
        "course_id": COURSE_ID,
        "course_version": COURSE_VERSION,
        "forward_axis": "-X",
        "route_side_axis": "engine-selected side around two centerline obstacles",
        "blocker_centers_m": [list(center) for center in BLOCKER_CENTERS_M],
        "blocker_half_extents_m": list(BLOCKER_HALF_EXTENTS_M),
        "finish_line_x_m": FINISH_LINE_X_M,
        "goal_center_m": list(GOAL_CENTER_M),
        "success_thresholds": {
            "min_forward_progress_m": MIN_FORWARD_PROGRESS_M,
            "first_clearance_offset_m": FIRST_CLEARANCE_OFFSET_M,
            "second_clearance_offset_m": SECOND_CLEARANCE_OFFSET_M,
            "first_obstacle_passed_progress_m": FIRST_OBSTACLE_PASSED_PROGRESS_M,
            "max_approach_clearance_m": MAX_APPROACH_CLEARANCE_M,
            "require_finish_line_crossing": True,
            "require_zero_blocker_contact": True,
        },
    }


def fingerprint() -> str:
    encoded = json.dumps(spec(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def horizontal_clearance_m(position: tuple[float, float] | list[float]) -> float:
    """Distance from a measured base point to the nearest physical box face."""

    half_x, half_y, _ = BLOCKER_HALF_EXTENTS_M
    px, py = float(position[0]), float(position[1])
    return min(
        math.hypot(max(abs(px - bx) - half_x, 0.0), max(abs(py - by) - half_y, 0.0))
        for bx, by in BLOCKER_CENTERS_M
    )


def mujoco_blocker_xml() -> str:
    """Render profile-owned obstacle bodies from the shared course contract."""

    half_x, half_y, half_z = BLOCKER_HALF_EXTENTS_M
    blocks = []
    for index, (x, y) in enumerate(BLOCKER_CENTERS_M, start=1):
        blocks.append(
            f'''      <body name="course_blocker_{index}" pos="{x} {y} {half_z}">
        <geom name="course_blocker_{index}_collision" type="box" size="{half_x} {half_y} {half_z}"
              rgba="0.92 0.16 0.05 1" friction="1.0 0.01 0.001" contype="1" conaffinity="1"/>
        <geom type="cylinder" pos="0 0 0.24" size="0.045 0.055" rgba="1 0.88 0.10 1" contype="0" conaffinity="0"/>
      </body>'''
        )
    return "\n".join(blocks)


def mujoco_finish_marker_xml() -> str:
    """Render a visible, non-colliding finish stripe from the course contract."""

    goal_x, goal_y = GOAL_CENTER_M
    return f'''      <geom name="course_finish_marker" type="box" pos="{goal_x} {goal_y} 0.006"
            size="0.025 0.33 0.006" rgba="0.08 0.82 0.35 0.92" contype="0" conaffinity="0"/>
      <geom type="cylinder" pos="{goal_x} {goal_y} 0.035" size="0.075 0.025"
            rgba="0.12 1 0.48 0.96" contype="0" conaffinity="0"/>'''


def webots_blockers_vrml() -> str:
    """Render the exact same box geometry for the generated Webots world."""

    half_x, half_y, half_z = BLOCKER_HALF_EXTENTS_M
    size_x, size_y, size_z = (2.0 * half_x, 2.0 * half_y, 2.0 * half_z)
    blocks = []
    for index, (x, y) in enumerate(BLOCKER_CENTERS_M, start=1):
        blocks.append(
            f'''DEF CENTRAL_RED_BLOCKER_{index} Solid {{
  translation {x} {y} {half_z}
  children [
    Shape {{
      appearance PBRAppearance {{
        baseColor 0.92 0.16 0.05
        roughness 0.45
        metalness 0.10
      }}
      geometry Box {{ size {size_x} {size_y} {size_z} }}
    }}
    Pose {{
      translation 0 0 {half_z + 0.055}
      children [
        Shape {{
          appearance PBRAppearance {{
            baseColor 1 0.88 0.10
            emissiveColor 0.25 0.19 0.01
          }}
          geometry Cylinder {{ radius 0.045 height 0.11 }}
        }}
      ]
    }}
  ]
  boundingObject Box {{ size {size_x} {size_y} {size_z} }}
}}'''
        )
    return "\n".join(blocks)


def webots_finish_marker_vrml() -> str:
    """Render a named goal beyond the physical posts; it has no authority."""

    goal_x, goal_y = GOAL_CENTER_M
    return (
        "DEF COURSE_FINISH_MARKER Pose {\n"
        f"  translation {goal_x} {goal_y} 0.006\n"
        "  children [\n"
        "    Shape {\n"
        "      appearance PBRAppearance { baseColor 0.08 0.82 0.35 emissiveColor 0.02 0.25 0.08 }\n"
        "      geometry Box { size 0.05 0.66 0.012 }\n"
        "    }\n"
        "    Pose {\n"
        "      translation 0 0 0.04\n"
        "      children [\n"
        "        Shape {\n"
        "          appearance PBRAppearance { baseColor 0.12 1 0.48 emissiveColor 0.02 0.30 0.10 }\n"
        "          geometry Cylinder { radius 0.075 height 0.05 }\n"
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )
