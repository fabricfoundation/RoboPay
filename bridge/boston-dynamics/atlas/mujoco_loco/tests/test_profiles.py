import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import ROBOTS, stand_z, hip_z
import flow.profiles as P
import pytest

RID = "boston-dynamics-atlas"


def test_robot_id_matches_engine():
    assert P.robot_id() == RID
    assert RID in ROBOTS


def test_skills_present():
    ids = P.skill_ids()
    assert ids == ["move_forward", "navigate_obstacle", "stop"]


def test_payment_policy_complete():
    req = P.payment_requirements("move_forward")[0]
    assert req["network"] == "eip155:84532"
    assert req["asset"] == "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
    assert req["amount"] == "0.10"
    assert P.settle_on_failure_allowed() is False


def test_kinematics_match_engine():
    prof = P.robot_profile()
    emb = prof["embodiment"]["kinematics"]
    c = ROBOTS[RID]
    assert abs(float(emb["standingHeight"]) - stand_z(c)) < 1e-3
    assert abs(float(emb["hipHeight"]) - hip_z(c)) < 1e-3
