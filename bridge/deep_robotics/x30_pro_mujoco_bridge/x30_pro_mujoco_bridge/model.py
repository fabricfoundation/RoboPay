"""Load the immutable official X30 source with a documented profile overlay."""

from __future__ import annotations

import os
from pathlib import Path

from .course import mujoco_blocker_xml, mujoco_finish_marker_xml


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
LOCAL_MODEL_DIR = PACKAGE_ROOT / "models" / "deep_robotics_x30"
MJCF_RELATIVE_PATH = Path("mjcf") / "X30.xml"
URDF_RELATIVE_PATH = Path("urdf") / "X30.urdf"
TORSO_BODY = "TORSO"
JOINT_NAMES = (
    "FL_HipX_joint", "FL_HipY_joint", "FL_Knee_joint",
    "FR_HipX_joint", "FR_HipY_joint", "FR_Knee_joint",
    "HL_HipX_joint", "HL_HipY_joint", "HL_Knee_joint",
    "HR_HipX_joint", "HR_HipY_joint", "HR_Knee_joint",
)
PROFILE_INITIAL_BASE_HEIGHT_M = 0.51


def _apply_presentation_materials(xml: str) -> str:
    """Color only the rendered vendor meshes in the in-memory scene.

    The official STL bundle has no paint/material metadata, so MuJoCo renders
    every mesh grey. These RGBA values make the original geometry legible for
    inspection without replacing a mesh, modifying a joint, or changing any
    collision/inertial property in the locked source files.
    """

    replacements = {
        'mesh="torso"/>': 'mesh="torso" rgba="0.94 0.95 0.96 1"/>',
        'mesh="hip"/>': 'mesh="hip" rgba="0.16 0.18 0.21 1"/>',
        'mesh="hip1"/>': 'mesh="hip1" rgba="0.16 0.18 0.21 1"/>',
        'mesh="hip2"/>': 'mesh="hip2" rgba="0.16 0.18 0.21 1"/>',
        'mesh="hip3"/>': 'mesh="hip3" rgba="0.16 0.18 0.21 1"/>',
        'mesh="thigh"/>': 'mesh="thigh" rgba="0.72 0.75 0.78 1"/>',
        'mesh="thigh1"/>': 'mesh="thigh1" rgba="0.72 0.75 0.78 1"/>',
        'mesh="shank"/>': 'mesh="shank" rgba="0.20 0.23 0.27 1"/>',
    }
    expected_counts = {
        'mesh="torso"/>': 1,
        'mesh="hip"/>': 1,
        'mesh="hip1"/>': 1,
        'mesh="hip2"/>': 1,
        'mesh="hip3"/>': 1,
        'mesh="thigh"/>': 2,
        'mesh="thigh1"/>': 2,
        'mesh="shank"/>': 4,
    }
    for original, replacement in replacements.items():
        if xml.count(original) != expected_counts[original]:
            raise RuntimeError(f"Pinned X30 MJCF mesh structure changed for {original}")
        xml = xml.replace(original, replacement)
    # The vendor MJCF keeps simplified collision solids alongside the rendered
    # STL meshes. They must remain in the physics model, but rendering them
    # creates the misleading white box/cylinders over the actual robot body.
    xml = xml.replace('<geom size=', '<geom rgba="0 0 0 0" size=')
    return xml


def resolve_model_dir(model_dir: str | Path | None = None) -> Path:
    candidates = [Path(model_dir)] if model_dir else []
    if configured := os.environ.get("X30_PRO_MODEL_DIR"):
        candidates.append(Path(configured))
    candidates.append(LOCAL_MODEL_DIR)
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if (candidate / MJCF_RELATIVE_PATH).is_file() and (candidate / URDF_RELATIVE_PATH).is_file():
            return candidate
    raise FileNotFoundError(
        "Pinned X30 model was not found. Run "
        "python bridge/deep_robotics/x30_pro_mujoco_bridge/download_x30_model.py "
        "or set X30_PRO_MODEL_DIR."
    )


def _profile_overlay_xml(source_dir: Path) -> str:
    """Keep vendor geometry/inertia immutable while adding documented simulation actuation.

    The public X30 MJCF defines links and passive joints but no free base or
    actuators.  This profile-only overlay adds a free joint, a collision floor,
    and bounded position actuators for those existing vendor joints.  It never
    writes a robot pose at runtime or changes vendor meshes, inertias, limits,
    or collision geometry on disk.
    """

    xml = (source_dir / MJCF_RELATIVE_PATH).read_text(encoding="utf-8")
    if 'meshdir="./meshes/"' not in xml or '<body name="TORSO">' not in xml:
        raise RuntimeError("Pinned X30 MJCF structure changed unexpectedly")
    mesh_dir = (source_dir / "mjcf" / "meshes").resolve().as_posix()
    xml = xml.replace('meshdir="./meshes/"', f'meshdir="{mesh_dir}"', 1)
    xml = _apply_presentation_materials(xml)
    torso = f'<body name="TORSO" pos="0 0 {PROFILE_INITIAL_BASE_HEIGHT_M}"><freejoint name="x30_profile_freejoint"/>'
    xml = xml.replace('<body name="TORSO">', torso, 1)
    environment = f'''
      <geom name="x30_profile_floor" type="plane" pos="0 0 0" size="8 8 0.1"
            rgba="0.14 0.16 0.18 1" friction="1.1 0.01 0.001" contype="1" conaffinity="1"/>
      <!-- Generated from the canonical profile-owned course contract. -->
{mujoco_blocker_xml()}
      <!-- Visual-only finish marker generated from the same course contract. -->
{mujoco_finish_marker_xml()}
    '''
    xml = xml.replace("<worldbody>", "<worldbody>" + environment, 1)
    actuators = "\n".join(
        f'<motor name="{joint}" joint="{joint}" gear="1" forcerange="-{limit} {limit}"/>'
        for joint in JOINT_NAMES
        for limit in (150 if joint.endswith("Knee_joint") else 84,)
    )
    xml = xml.replace("</mujoco>", f"<actuator>{actuators}</actuator></mujoco>", 1)
    return xml


def load_mujoco_inspection_model(model_dir: str | Path | None = None):
    import mujoco

    return mujoco.MjModel.from_xml_string(_profile_overlay_xml(resolve_model_dir(model_dir)))
