"""The robot must be the pinned Atlas v4, and the code must address it correctly.

These tests exist because of a real defect: an earlier revision kept a
hand-written actuator order that disagreed with the compiled model, so 27 of 30
control channels were cross-wired.  Anything that could let that happen again is
asserted here.
"""

from __future__ import annotations

import json
from pathlib import Path

import mujoco
import pytest

from bridge.boston_dynamics.atlas_bridge import actuators, model
from bridge.boston_dynamics.atlas_bridge.mujoco_env import AtlasInspectionEnvironment
from bridge.boston_dynamics.atlas_bridge.task import (
    END_EFFECTOR_BODY,
    HOME_END_EFFECTOR,
    HOME_PELVIS_HEIGHT_M,
    INSPECTION_CHAIN,
    STANCE_POSE,
)

LOCK = json.loads(
    (Path(model.__file__).parent / "models" / "model.lock.json").read_text(encoding="utf-8")
)["atlas_v4"]


def test_model_source_is_pinned_and_licensed():
    assert LOCK["commit"] == "d32bcb2b35b94168b5ce27233ca62f3c8678886f"
    assert LOCK["license"] == "MIT"
    assert LOCK["source"].endswith("roboschool")


def test_no_model_assets_are_vendored():
    """The description is fetched, never committed."""
    tracked = Path(model.__file__).parent / "models"
    committed = [p for p in tracked.rglob("*") if p.is_file() and p.name != "model.lock.json"]
    vendored = [p for p in committed if "atlas_v4" not in p.parts]
    assert vendored == [], f"unexpected vendored model assets: {vendored}"


def test_actuator_map_matches_the_urdf():
    """Effort limits and joint set come from the URDF, not from a copy."""
    compiled = mujoco.MjModel.from_xml_string(model.scene_xml())
    mapped = actuators.validate(compiled, model.joint_efforts())
    assert len(mapped) == 30
    # Values below are the upstream Atlas v4 URDF's own effort limits.  They are
    # asserted rather than re-declared anywhere in the bridge: the previous
    # revision hard-coded a different (v5) table and silently disagreed with the
    # model it was driving.
    assert mapped.effort_limits[mapped.index("l_leg_kny")] == pytest.approx(890.0)
    assert mapped.effort_limits[mapped.index("l_leg_hpy")] == pytest.approx(840.0)
    assert mapped.effort_limits[mapped.index("l_leg_aky")] == pytest.approx(92.0)
    assert mapped.effort_limits[mapped.index("r_arm_elx")] == pytest.approx(112.0)


def test_actuator_validation_fails_loudly_on_drift():
    """A joint-set or effort mismatch must raise, never pass silently."""
    compiled = mujoco.MjModel.from_xml_string(model.scene_xml())
    drifted = dict(model.joint_efforts())
    drifted["l_leg_kny"] = 80.0
    with pytest.raises(ValueError, match="Effort limit drift"):
        actuators.validate(compiled, drifted)

    missing = dict(model.joint_efforts())
    missing.pop("r_arm_elx")
    with pytest.raises(ValueError, match="does not match the pinned URDF"):
        actuators.validate(compiled, missing)


def test_control_vector_is_addressed_by_name_not_position():
    """``vector`` must place each value on that joint's own ctrl channel."""
    compiled = mujoco.MjModel.from_xml_string(model.scene_xml())
    mapped = actuators.build(compiled)
    command = mapped.vector({"l_leg_kny": 0.62, "r_arm_elx": -1.2})
    assert command[mapped.index("l_leg_kny")] == pytest.approx(0.62)
    assert command[mapped.index("r_arm_elx")] == pytest.approx(-1.2)
    assert command[mapped.index("back_bkz")] == pytest.approx(0.0)


def test_unknown_joint_names_are_rejected():
    compiled = mujoco.MjModel.from_xml_string(model.scene_xml())
    mapped = actuators.build(compiled)
    with pytest.raises(KeyError):
        mapped.vector({"not_an_atlas_joint": 1.0})


def test_inspection_chain_and_end_effector_exist_on_the_robot():
    compiled = mujoco.MjModel.from_xml_string(model.scene_xml())
    names = set(actuators.build(compiled).names)
    assert set(INSPECTION_CHAIN) <= names
    assert set(STANCE_POSE) <= names
    assert mujoco.mj_name2id(compiled, mujoco.mjtObj.mjOBJ_BODY, END_EFFECTOR_BODY) >= 0


def test_home_pose_matches_the_recorded_geometry():
    """Guards the shelf coordinates against silent model drift."""
    environment = AtlasInspectionEnvironment()
    environment.reset(dict(STANCE_POSE))
    for _ in range(1200):
        environment.step(dict(STANCE_POSE))
    hand = environment.end_effector()
    for measured, recorded in zip(hand, HOME_END_EFFECTOR):
        assert measured == pytest.approx(recorded, abs=0.02)
    assert environment.observe()["pelvis_height"] == pytest.approx(
        HOME_PELVIS_HEIGHT_M, abs=0.02
    )
