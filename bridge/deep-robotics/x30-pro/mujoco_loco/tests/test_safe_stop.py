from engine import Simulator
import pytest

RID = "deep-robotics-x30-pro"


def test_stop_is_bounded():
    sim = Simulator(RID)
    r = sim.stop()
    assert r.success is True
    # stop must terminate within its step budget and not drift
    assert r.metrics["stepsUsed"] <= r.metrics["stepBudget"]
    assert abs(r.metrics["distanceTraveled"]) < 0.2
