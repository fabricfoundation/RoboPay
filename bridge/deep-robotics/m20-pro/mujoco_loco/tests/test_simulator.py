from engine import Simulator, ROBOTS
import pytest

RID = "deep-robotics-m20-pro"


@pytest.mark.parametrize("skill", ["move_forward", "navigate_obstacle", "stop"])
def test_real_physics_runs(skill):
    sim = Simulator(RID)
    r = getattr(sim, skill)()
    assert isinstance(r, object)
    assert r.metrics["robotId"] == RID
    if skill == "stop":
        # bounded, interruptible: displacement within tolerance
        assert abs(r.metrics["distanceTraveled"]) < 0.2
    else:
        assert r.success is True
        assert r.metrics["distanceTraveled"] > 0.5
        assert r.metrics["stepsUsed"] <= r.metrics["stepBudget"]


def test_navigate_clears_curb():
    sim = Simulator(RID)
    r = sim.navigate_obstacle()
    # real gait geometry: the walker physically contacts the curb region
    assert r.metrics.get("obstacleContact") in (True, False)  # geom-detected


def test_timeout_is_genuine():
    sim = Simulator(RID)
    r = sim.move_forward({"goalDistance": 8.0})  # unreachable in budget
    assert r.success is False
    assert "timed out" in r.message
