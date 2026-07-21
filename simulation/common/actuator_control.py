"""
MuJoCo simulation runner with proper actuator control.

Uses MuJoCo actuators for humanoid locomotion instead of
direct velocity manipulation (which bypasses physics).

For humanoid robots (G1, Atlas, TRON, etc.):
  - Position actuators control joint angles
  - Free joint is controlled via body forces/torques

For quadruped robots (Go2, Spot, M20, X30):
  - Joint actuators for leg motion
  - Base velocity through body forces
"""
import json
import math
from pathlib import Path
from typing import Tuple

import numpy as np

try:
    import mujoco
    MUJOCO_AVAILABLE = True
except ImportError:
    MUJOCO_AVAILABLE = False


def get_actuator_id(model, name: str) -> int:
    """Get actuator ID by name, return -1 if not found."""
    try:
        return mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
    except Exception:
        return -1


def apply_locomotion_control(
    model,
    data,
    forward_speed: float,
    angular_speed: float,
    heading: float,
):
    """Apply locomotion control through MuJoCo actuators.

    This properly uses the physics engine instead of bypassing it.
    """
    # Find root actuators
    root_x = get_actuator_id(model, "root_x")
    root_y = get_actuator_id(model, "root_y")
    root_yaw = get_actuator_id(model, "root_yaw")

    if root_x >= 0 and root_y >= 0:
        # Direct force control on free joint
        vx = forward_speed * math.cos(heading)
        vy = forward_speed * math.sin(heading)
        data.ctrl[root_x] = vx * 50.0  # Scale to force
        data.ctrl[root_y] = vy * 50.0
    if root_yaw >= 0:
        data.ctrl[root_yaw] = angular_speed * 30.0

    # For humanoid: maintain upright posture via joint position targets
    for joint_name in ["left_hip", "right_hip"]:
        jid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        if jid >= 0:
            # Find corresponding actuator
            for i in range(model.nu):
                if model.actuator_trnid[i][0] == jid:
                    data.ctrl[i] = 0.0  # Neutral position
                    break


def detect_contacts(model, data, min_force: float = 1.0):
    """Detect contacts from MuJoCo physics engine."""
    contacts = []
    for i in range(data.ncon):
        force = np.zeros(6)
        mujoco.mj_contactForce(model, data, i, force)
        f = np.linalg.norm(force[:3])
        if f > min_force:
            contacts.append({
                "force": float(f),
                "body1": model.geom(data.contact[i].geom1).name,
                "body2": model.geom(data.contact[i].geom2).name,
            })
    return contacts


def get_body_state(model, data, body_name: str):
    """Get body position and velocity from simulator."""
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body_name)
    if bid < 0:
        return None
    return {
        "position": data.xpos[bid].tolist(),
        "velocity": data.cvel[bid][:3].tolist(),
        "quaternion": data.xquat[bid].tolist(),
    }


def heading_from_quaternion(quat):
    """Extract heading angle from quaternion."""
    siny_cosp = 2 * (quat[0] * quat[3] + quat[1] * quat[2])
    cosy_cosp = 1 - 2 * (quat[2] ** 2 + quat[3] ** 2)
    return math.atan2(siny_cosp, cosy_cosp)
