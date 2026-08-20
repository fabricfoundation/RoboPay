"""The shared kinematics must agree with the physics engine's own.

The controller uses one URDF-derived Jacobian in every simulator so that the
"same controller everywhere" claim is literally true.  That only holds if this
Jacobian is correct, so it is checked against MuJoCo's independently computed
one here.
"""

from __future__ import annotations

import numpy as np
import pytest

from bridge.boston_dynamics.atlas_bridge import kinematics
from bridge.boston_dynamics.atlas_bridge.mujoco_env import AtlasInspectionEnvironment
from bridge.boston_dynamics.atlas_bridge.task import (
    END_EFFECTOR_BODY,
    INSPECTION_CHAIN,
    STANCE_POSE,
)


@pytest.fixture(scope="module")
def settled() -> AtlasInspectionEnvironment:
    environment = AtlasInspectionEnvironment()
    environment.reset(dict(STANCE_POSE))
    for _ in range(1200):
        environment.step(dict(STANCE_POSE))
    return environment


def test_chain_reaches_the_end_effector():
    chain = kinematics.chain_to_end_effector()
    assert chain[-1].child == END_EFFECTOR_BODY
    names = {link.name for link in chain}
    assert set(INSPECTION_CHAIN) <= names


def test_forward_kinematics_matches_mujoco(settled):
    angles = settled.joint_angles()
    predicted = kinematics.end_effector_position(
        angles,
        base_position=settled.data.xpos[settled.pelvis_id],
        base_rotation=settled.base_rotation(),
    )
    measured = settled.end_effector()
    assert np.linalg.norm(predicted - measured) < 0.002


def test_jacobian_matches_mujoco(settled):
    ours = kinematics.jacobian(
        settled.joint_angles(), base_rotation=settled.base_rotation()
    )
    theirs = settled.engine_jacobian()
    assert ours.shape == (3, len(INSPECTION_CHAIN))
    # The measured worst case over an episode is 2.4e-4; the bound is kept just
    # above it so the PR description and this test cannot drift apart.
    assert np.max(np.abs(ours - theirs)) < 5e-4


def test_jacobian_tracks_configuration_changes(settled):
    """A different arm configuration has to give a different Jacobian."""
    angles = settled.joint_angles()
    moved = dict(angles)
    moved["r_arm_ely"] = angles["r_arm_ely"] + 0.5
    assert not np.allclose(kinematics.jacobian(angles), kinematics.jacobian(moved))


def test_unknown_chain_joint_is_rejected(settled):
    with pytest.raises(KeyError):
        kinematics.jacobian(settled.joint_angles(), chain=("l_leg_kny",))


def test_gravity_model_matches_mujoco(settled):
    """The shared gravity feedforward must equal MuJoCo's own bias term.

    Every backend uses this model — MuJoCo has ``qfrc_bias`` and PyBullet has
    inverse dynamics, but Webots has neither, so the feedforward is computed from
    the URDF instead.  That is only legitimate while it agrees with an engine
    that computes it independently.
    """
    import mujoco

    settled.data.qvel[:] = 0.0
    mujoco.mj_forward(settled.model, settled.data)
    theirs = settled.data.qfrc_bias[settled.actuators.qvel_addresses]

    ours = kinematics.gravity_torques(
        settled.joint_angles(), base_rotation=settled.base_rotation()
    )
    for name, expected in zip(settled.actuators.names, theirs):
        assert ours[name] == pytest.approx(float(expected), abs=1e-3)


def test_gravity_model_covers_every_actuated_joint(settled):
    torques = kinematics.gravity_torques(settled.joint_angles())
    assert set(settled.actuators.names) <= set(torques)


def test_gravity_model_responds_to_configuration(settled):
    """An extended arm must load the shoulder more than a tucked one."""
    tucked = settled.joint_angles()
    extended = dict(tucked)
    extended["r_arm_shx"] = 0.0  # arm swung out horizontally
    assert abs(kinematics.gravity_torques(extended)["r_arm_shx"]) > abs(
        kinematics.gravity_torques(tucked)["r_arm_shx"]
    )
