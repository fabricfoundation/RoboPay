import pytest

pytest.importorskip("pybullet")
from engine import Simulator
from simulator_pybullet import PyBulletSimulator

RID = "deep-robotics-x30-pro"


def test_mujoco_pybullet_agree():
    mj = Simulator(RID).move_forward()
    pb = PyBulletSimulator().move_forward()
    # same controller => comparable travelled distance within tolerance
    assert abs(mj.metrics["distanceTraveled"] - pb.metrics["distanceTraveled"]) < 0.5
