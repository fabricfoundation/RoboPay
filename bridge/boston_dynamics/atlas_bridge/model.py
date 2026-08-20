"""Build the Atlas v4 MJCF used by the MuJoCo side of the bridge.

The MJCF is generated from the pinned upstream URDF (see ``NOTICE.md``) so the
robot, its joint limits and its actuator efforts always come from one source of
truth.  Nothing about the robot is hand-transcribed here:

* joint effort limits become motor ``gear`` values, read out of the URDF;
* the actuator set is generated from the URDF joints, in URDF order;
* only simulation-side details the URDF cannot express (free base, joint
  armature, ground plane) are added by this module.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco

from .download_atlas_model import urdf_path

# Rotor inertia reflected through the gearbox.  The URDF has no armature and a
# 182 kg humanoid on stiff joint servos is numerically stiff without it.
JOINT_ARMATURE = 0.05
# Pelvis spawn height; the environment drops the robot onto its soles from here.
SPAWN_HEIGHT_M = 1.20

_MUJOCO_COMPILER = (
    '<mujoco><compiler discardvisual="true" balanceinertia="true" '
    'fusestatic="false" strippath="true"/></mujoco>'
)


def joint_efforts() -> dict[str, float]:
    """Map every actuated Atlas joint to its URDF effort limit in N·m."""
    root = ET.parse(urdf_path()).getroot()
    efforts: dict[str, float] = {}
    for joint in root.findall("joint"):
        if joint.get("type") in (None, "fixed"):
            continue
        limit = joint.find("limit")
        if limit is None or limit.get("effort") is None:
            continue
        efforts[joint.get("name", "")] = float(limit.get("effort", "0"))
    return efforts


def physics_urdf() -> Path:
    """Write and return a physics-only copy of the pinned Atlas URDF.

    ``<visual>`` elements are dropped because the upstream description points
    them at COLLADA meshes from a sibling ROS package, while all collision
    geometry is analytic (boxes, cylinders, spheres).  Removing them lets MuJoCo,
    PyBullet and Webots consume the *same* file with no per-engine asset
    handling, which is what makes the three runs one robot.
    """
    source = urdf_path()
    staged = source.with_name("atlas_v4_physics.urdf")
    tree = ET.parse(source)
    root = tree.getroot()
    for link in root.iter("link"):
        for visual in link.findall("visual"):
            link.remove(visual)
    tree.write(staged, encoding="unicode", xml_declaration=True)
    return staged


def _mjcf_from_urdf() -> str:
    """Import the pinned URDF through MuJoCo and return it as MJCF text."""
    source = physics_urdf().read_text(encoding="utf-8")
    patched = re.sub(r"(<robot\b[^>]*>)", r"\1\n" + _MUJOCO_COMPILER, source, count=1)
    staged = urdf_path().with_name("_robopay_mujoco.urdf")
    staged.write_text(patched, encoding="utf-8")
    try:
        model = mujoco.MjModel.from_xml_path(str(staged))
        target = staged.with_suffix(".xml")
        mujoco.mj_saveLastXML(str(target), model)
        return target.read_text(encoding="utf-8")
    finally:
        staged.unlink(missing_ok=True)


def scene_xml(free_base: bool = True) -> str:
    """Return the complete MJCF scene: Atlas v4 plus ground plane and motors.

    ``free_base=True`` gives the free-standing robot used for the bounty task.
    ``free_base=False`` welds the pelvis and is used only by component tests.
    """
    xml = _mjcf_from_urdf()

    if free_base:
        xml = xml.replace(
            '<body name="pelvis">',
            f'<body name="pelvis" pos="0 0 {SPAWN_HEIGHT_M}">\n<freejoint name="root"/>',
            1,
        )
    xml = re.sub(
        r"(<joint\b(?![^>]*\barmature=)[^>]*?)/>",
        rf'\1 armature="{JOINT_ARMATURE}"/>',
        xml,
    )

    motors = "".join(
        f'    <motor name="{name}_motor" joint="{name}" gear="{effort:g}" ctrlrange="-1 1"/>\n'
        for name, effort in joint_efforts().items()
    )
    extras = (
        # Large enough offscreen buffer for the evidence renderer.
        '  <visual>\n    <global offwidth="1280" offheight="960"/>\n  </visual>\n'
        f"  <actuator>\n{motors}  </actuator>\n"
        '  <worldbody>\n'
        '    <geom name="floor" type="plane" size="20 20 0.1" condim="3"\n'
        '          friction="1.1 0.02 0.002" rgba="0.35 0.37 0.40 1"/>\n'
        '    <light pos="1.2 0 3.2" dir="0 0 -1" diffuse="0.8 0.8 0.8"/>\n'
        '    <light pos="-1.5 1.5 2.5" dir="0.5 -0.5 -1" diffuse="0.35 0.35 0.35"/>\n'
        "  </worldbody>\n"
    )
    return xml.replace("</mujoco>", extras + "</mujoco>")


def write_scene(destination: Path, free_base: bool = True) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(scene_xml(free_base=free_base), encoding="utf-8")
    return destination
