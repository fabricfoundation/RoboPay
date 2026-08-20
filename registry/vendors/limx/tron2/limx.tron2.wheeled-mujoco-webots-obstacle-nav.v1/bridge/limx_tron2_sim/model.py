"""Pinned LimX model paths and generated MuJoCo obstacle scene."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

from .course import GOAL, OBSTACLES, WAYPOINTS


PROFILE_ROOT = Path(__file__).resolve().parents[2]
DESCRIPTION_ROOT = PROFILE_ROOT / "vendor" / "limx-tron2" / "robot-description"
ROBOT_ROOT = DESCRIPTION_ROOT / "WF_TRON2A"
MUJOCO_XML = ROBOT_ROOT / "xml" / "robot.xml"
URDF = ROBOT_ROOT / "urdf" / "robot.urdf"
MESHES = ROBOT_ROOT / "meshes"
POLICY_ROOT = (
    PROFILE_ROOT
    / "vendor"
    / "limxdynamics"
    / "tron2_rl_deploy_python"
    / "controllers"
    / "model"
    / "WF_TRON2A"
)
POLICY_ONNX = POLICY_ROOT / "policy.onnx"
ENCODER_ONNX = POLICY_ROOT / "encoder.onnx"
POLICY_PARAMS = POLICY_ROOT / "params.yaml"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_mujoco_scene_xml() -> str:
    """Compose the locked vendor MJCF with a profile-owned physical course."""

    root = ET.fromstring(MUJOCO_XML.read_text(encoding="utf-8"))
    compiler = root.find("compiler")
    if compiler is None:
        compiler = ET.SubElement(root, "compiler")
    compiler.set("meshdir", MESHES.as_posix())
    compiler.set("angle", "radian")
    option = root.find("option")
    if option is not None:
        option.set("gravity", "0 0 -9.81")
        option.set("integrator", "implicitfast")
    world = root.find("worldbody")
    if world is None:
        raise RuntimeError("vendor MJCF has no worldbody")
    ET.SubElement(
        world,
        "camera",
        name="course_overview",
        pos="2.2 -5.4 3.2",
        xyaxes="1 0 0 0 0.52 0.85",
        fovy="48",
    )
    ET.SubElement(world, "light", directional="true", pos="2 -2 6", dir="0.1 0.1 -1", diffuse="0.8 0.8 0.8")
    for obstacle in OBSTACLES:
        ET.SubElement(
            world,
            "geom",
            name=f"course_{obstacle.name}",
            type="box",
            pos=f"{obstacle.x} {obstacle.y} {obstacle.height / 2.0}",
            size=f"{obstacle.half_x} {obstacle.half_y} {obstacle.height / 2.0}",
            rgba=" ".join(str(value) for value in obstacle.color),
            contype="1",
            conaffinity="1",
            friction="0.9 0.01 0.001",
        )
    for index, (x, y) in enumerate(WAYPOINTS):
        ET.SubElement(
            world,
            "geom",
            name=f"waypoint_{index}",
            type="cylinder",
            pos=f"{x} {y} 0.008",
            size="0.10 0.008",
            rgba="0.18 0.82 0.32 0.55",
            contype="0",
            conaffinity="0",
        )
    ET.SubElement(
        world,
        "geom",
        name="goal_marker",
        type="cylinder",
        pos=f"{GOAL[0]} {GOAL[1]} 0.025",
        size="0.28 0.025",
        rgba="0.12 0.95 0.28 0.9",
        contype="0",
        conaffinity="0",
    )
    return ET.tostring(root, encoding="unicode")
