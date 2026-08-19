"""Analytic forward kinematics and Jacobian for the inspection arm.

The controller needs a Jacobian every control step.  MuJoCo and PyBullet can each
supply one, but Webots cannot, and three engine-specific Jacobians would mean the
"same controller" claim quietly stopped being true.

So the Jacobian is computed here instead, straight from the pinned URDF: same
joint origins, same axes, same chain, in every simulator.  Only the *dynamics*
then differ between engines, which is exactly what the sim-to-sim comparison is
meant to measure.  ``tests/test_kinematics.py`` checks this against MuJoCo's own
Jacobian so the two can never silently diverge.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .download_atlas_model import urdf_path
from .task import END_EFFECTOR_BODY, INSPECTION_CHAIN


def _rpy_to_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = np.cos(roll), np.sin(roll)
    cp, sp = np.cos(pitch), np.sin(pitch)
    cy, sy = np.cos(yaw), np.sin(yaw)
    return np.array(
        [
            [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr],
        ]
    )


def _axis_angle_to_matrix(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    skew = np.array(
        [[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]]
    )
    return np.eye(3) + np.sin(angle) * skew + (1.0 - np.cos(angle)) * (skew @ skew)


#: Gravitational acceleration, matching every simulator's world setting.
GRAVITY = 9.81


@dataclass(frozen=True)
class Link:
    """One URDF joint expressed as a fixed offset plus a rotation axis."""

    name: str
    parent: str
    child: str
    origin: np.ndarray
    rotation: np.ndarray
    axis: np.ndarray
    movable: bool


@dataclass(frozen=True)
class Inertial:
    """A link's mass and centre of mass, in the link's own frame."""

    mass: float
    com: np.ndarray


@lru_cache(maxsize=1)
def _joints() -> dict[str, Link]:
    root = ET.parse(urdf_path()).getroot()
    joints: dict[str, Link] = {}
    for joint in root.findall("joint"):
        origin = joint.find("origin")
        xyz = np.zeros(3)
        rpy = np.zeros(3)
        if origin is not None:
            xyz = np.array([float(v) for v in origin.get("xyz", "0 0 0").split()])
            rpy = np.array([float(v) for v in origin.get("rpy", "0 0 0").split()])
        axis_element = joint.find("axis")
        axis = (
            np.array([float(v) for v in axis_element.get("xyz", "1 0 0").split()])
            if axis_element is not None
            else np.array([1.0, 0.0, 0.0])
        )
        joints[joint.get("name", "")] = Link(
            name=joint.get("name", ""),
            parent=joint.find("parent").get("link", ""),
            child=joint.find("child").get("link", ""),
            origin=xyz,
            rotation=_rpy_to_matrix(*rpy),
            axis=axis,
            movable=joint.get("type") not in (None, "fixed"),
        )
    return joints


@lru_cache(maxsize=1)
def chain_to_end_effector() -> tuple[Link, ...]:
    """Ordered joints from the pelvis down to the end-effector link."""
    joints = _joints()
    by_child = {link.child: link for link in joints.values()}
    path: list[Link] = []
    cursor = END_EFFECTOR_BODY
    while cursor in by_child:
        link = by_child[cursor]
        path.append(link)
        cursor = link.parent
    return tuple(reversed(path))


def forward_kinematics(joint_angles: dict[str, float]) -> tuple[np.ndarray, list[tuple[str, np.ndarray, np.ndarray]]]:
    """End-effector position in the pelvis frame, plus each joint's frame.

    Returns ``(position, frames)`` where ``frames`` holds
    ``(joint_name, world_axis, world_origin)`` for every movable joint on the
    chain, which is all the Jacobian needs.
    """
    position = np.zeros(3)
    rotation = np.eye(3)
    frames: list[tuple[str, np.ndarray, np.ndarray]] = []
    for link in chain_to_end_effector():
        position = position + rotation @ link.origin
        rotation = rotation @ link.rotation
        if link.movable:
            axis_world = rotation @ link.axis
            frames.append((link.name, axis_world, position.copy()))
            rotation = rotation @ _axis_angle_to_matrix(link.axis, joint_angles.get(link.name, 0.0))
    return position, frames


def jacobian(
    joint_angles: dict[str, float],
    chain: tuple[str, ...] = INSPECTION_CHAIN,
    base_rotation: np.ndarray | None = None,
) -> np.ndarray:
    """Positional Jacobian of the end effector w.r.t. ``chain``.

    ``base_rotation`` rotates the result out of the pelvis frame into the world
    frame; pass the pelvis orientation when the robot is free-standing.
    """
    position, frames = forward_kinematics(joint_angles)
    columns = []
    for name in chain:
        match = next((frame for frame in frames if frame[0] == name), None)
        if match is None:
            raise KeyError(f"{name} is not on the chain to {END_EFFECTOR_BODY}")
        _, axis_world, origin = match
        columns.append(np.cross(axis_world, position - origin))
    result = np.column_stack(columns)
    if base_rotation is not None:
        result = base_rotation @ result
    return result


def end_effector_position(
    joint_angles: dict[str, float],
    base_position: np.ndarray | None = None,
    base_rotation: np.ndarray | None = None,
) -> np.ndarray:
    """End-effector position, optionally transformed into the world frame."""
    position, _ = forward_kinematics(joint_angles)
    if base_rotation is not None:
        position = base_rotation @ position
    if base_position is not None:
        position = position + base_position
    return position


@lru_cache(maxsize=1)
def _inertials() -> dict[str, Inertial]:
    """Mass and centre of mass of every URDF link, in the link frame."""
    root = ET.parse(urdf_path()).getroot()
    inertials: dict[str, Inertial] = {}
    for link in root.findall("link"):
        inertial = link.find("inertial")
        if inertial is None:
            continue
        mass_element = inertial.find("mass")
        origin = inertial.find("origin")
        com = (
            np.array([float(v) for v in origin.get("xyz", "0 0 0").split()])
            if origin is not None
            else np.zeros(3)
        )
        inertials[link.get("name", "")] = Inertial(
            mass=float(mass_element.get("value", "0")) if mass_element is not None else 0.0,
            com=com,
        )
    return inertials


@lru_cache(maxsize=1)
def _tree() -> tuple[dict[str, list[Link]], str]:
    """Children by parent link, plus the root link name."""
    joints = _joints()
    children: dict[str, list[Link]] = {}
    for link in joints.values():
        children.setdefault(link.parent, []).append(link)
    child_links = {link.child for link in joints.values()}
    roots = [name for name in children if name not in child_links]
    return children, roots[0]


def link_poses(joint_angles: dict[str, float]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Position and orientation of every link, in the root (pelvis) frame."""
    children, root = _tree()
    poses: dict[str, tuple[np.ndarray, np.ndarray]] = {
        root: (np.zeros(3), np.eye(3))
    }
    stack = [root]
    while stack:
        parent = stack.pop()
        position, rotation = poses[parent]
        for link in children.get(parent, []):
            child_position = position + rotation @ link.origin
            child_rotation = rotation @ link.rotation
            if link.movable:
                child_rotation = child_rotation @ _axis_angle_to_matrix(
                    link.axis, joint_angles.get(link.name, 0.0)
                )
            poses[link.child] = (child_position, child_rotation)
            stack.append(link.child)
    return poses


@lru_cache(maxsize=1)
def _subtree_links() -> dict[str, tuple[str, ...]]:
    """Every link at or below each joint's child link."""
    children, _ = _tree()
    result: dict[str, tuple[str, ...]] = {}
    for joint in _joints().values():
        if not joint.movable:
            continue
        collected: list[str] = []
        stack = [joint.child]
        while stack:
            current = stack.pop()
            collected.append(current)
            stack.extend(link.child for link in children.get(current, []))
        result[joint.name] = tuple(collected)
    return result


def gravity_torques(
    joint_angles: dict[str, float], base_rotation: np.ndarray | None = None
) -> dict[str, float]:
    """Joint torques that hold the robot against gravity in this configuration.

    Model-based feedforward derived from the pinned URDF's own masses, so every
    simulator can run the identical servo law.  MuJoCo has ``qfrc_bias``,
    PyBullet has inverse dynamics and Webots has neither; computing it here once
    keeps the three backends honest about being the same controller.

    ``tests/test_kinematics.py`` checks the result against MuJoCo's own bias
    term at rest.
    """
    poses = link_poses(joint_angles)
    inertials = _inertials()
    rotation = np.eye(3) if base_rotation is None else np.asarray(base_rotation)
    gravity = rotation.T @ np.array([0.0, 0.0, -GRAVITY])

    torques: dict[str, float] = {}
    for joint in _joints().values():
        if not joint.movable or joint.child not in poses:
            continue
        joint_position, joint_rotation = poses[joint.child]
        # The joint frame's axis before its own rotation is applied is the same
        # axis expressed in the child frame, so use the child pose directly.
        axis_world = joint_rotation @ joint.axis
        torque = 0.0
        for link_name in _subtree_links()[joint.name]:
            inertial = inertials.get(link_name)
            if inertial is None or inertial.mass == 0.0:
                continue
            position, link_rotation = poses[link_name]
            com_world = position + link_rotation @ inertial.com
            force = inertial.mass * gravity
            lever = com_world - joint_position
            torque += float(np.dot(axis_world, np.cross(lever, force)))
        torques[joint.name] = -torque
    return torques
