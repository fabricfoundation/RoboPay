"""Locate the immutable vendor M20 MJCF source."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL_DIR = PACKAGE_ROOT / "models" / "deep_robotics_m20"
MJCF_RELATIVE_PATH = Path("mjcf") / "M20.xml"
URDF_RELATIVE_PATH = Path("urdf") / "M20.urdf"

# The course is a small profile-owned environment layered over, but never
# written back into, the locked vendor robot source. Dimensions are half
# extents in metres. The obstacle is a physical MuJoCo collision geometry
# moved only as a kinematic *environment* actor, never by changing robot state.
OBSTACLE_CENTER_X_M = 1.20
OBSTACLE_CENTER_Y_M = 0.0
OBSTACLE_HALF_LENGTH_M = 0.10
OBSTACLE_HALF_WIDTH_M = 0.12
OBSTACLE_HEIGHT_M = 0.28
OBSTACLE_CLEAR_Y_M = 1.0
# The vendor MJCF's demonstration pose starts the free base at 1 m.  The
# profile uses the same supported pose in both engines so the visual course
# begins near wheel contact instead of visibly dropping half a metre.
PROFILE_INITIAL_BASE_HEIGHT_M = 0.58


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    candidates = [Path(model_dir)] if model_dir else []
    if configured := os.environ.get("M20_PRO_MODEL_DIR"):
        candidates.append(Path(configured))
    candidates.append(LOCAL_MODEL_DIR)
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / MJCF_RELATIVE_PATH).is_file() and (candidate / URDF_RELATIVE_PATH).is_file():
            return candidate
    raise FileNotFoundError(
        "Pinned M20 model was not found. Run "
        "python bridge/deep_robotics/m20_pro_mujoco_bridge/download_m20_model.py "
        "or set M20_PRO_MODEL_DIR."
    )


def load_mujoco_model(model_dir: str | Path | None = None):
    """Compile the unmodified vendor MJCF with real MuJoCo."""

    import mujoco

    return mujoco.MjModel.from_xml_path(str(resolve_model_dir(model_dir) / MJCF_RELATIVE_PATH))


def load_mujoco_obstacle_course_model(model_dir: str | Path | None = None):
    """Compile the locked vendor robot with a profile-owned physical course.

    The vendor MJCF remains immutable on disk.  We add a single static box in
    memory and point the compiler at the verified vendor mesh directory.  This
    makes collision, contact and rendered geometry part of real MuJoCo physics
    instead of an animation overlay.
    """

    import mujoco

    source_dir = resolve_model_dir(model_dir)
    source = source_dir / MJCF_RELATIVE_PATH
    xml = source.read_text(encoding="utf-8")
    mesh_dir = (source.parent / "meshes").resolve().as_posix()
    if 'meshdir="./meshes/"' not in xml:
        raise RuntimeError("Pinned M20 MJCF compiler mesh directory changed unexpectedly")
    xml = xml.replace('meshdir="./meshes/"', f'meshdir="{mesh_dir}"', 1)
    vendor_initial_pose = '<body name="base_link" pos="0 0. 1."'
    profile_initial_pose = (
        f'<body name="base_link" pos="0 0 {PROFILE_INITIAL_BASE_HEIGHT_M}"'
    )
    if vendor_initial_pose not in xml:
        raise RuntimeError("Pinned M20 MJCF base-link initial pose changed unexpectedly")
    xml = xml.replace(vendor_initial_pose, profile_initial_pose, 1)
    obstacle = f'''
        <body name="obstacle_course_marker" mocap="true" pos="{OBSTACLE_CENTER_X_M} {OBSTACLE_CENTER_Y_M} 0">
            <geom name="course_obstacle" type="box"
                  pos="0 0 {OBSTACLE_HEIGHT_M}" size="{OBSTACLE_HALF_LENGTH_M} {OBSTACLE_HALF_WIDTH_M} {OBSTACLE_HEIGHT_M}"
                  rgba="0.92 0.22 0.12 1" contype="1" conaffinity="1" friction="1 0.01 0.001"/>
            <geom name="course_obstacle_beacon" type="cylinder"
                  pos="0 0 {2 * OBSTACLE_HEIGHT_M + 0.03}" size="0.045 0.03"
                  rgba="1 0.78 0.05 1" contype="0" conaffinity="0"/>
        </body>
        <geom name="course_goal_line" type="box" pos="1.40 0 0.004" size="0.018 0.72 0.004"
              rgba="0.15 0.85 0.28 0.75" contype="0" conaffinity="0"/>
    '''
    if "<worldbody>" not in xml:
        raise RuntimeError("Pinned M20 MJCF has no worldbody for obstacle course insertion")
    xml = xml.replace("<worldbody>", "<worldbody>" + obstacle, 1)
    return mujoco.MjModel.from_xml_string(xml)
