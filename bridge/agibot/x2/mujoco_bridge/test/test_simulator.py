from pathlib import Path
import sys

PACKAGE = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE))

from x2.simulator import X2Simulator


def _simulator():
    return X2Simulator(str(PACKAGE / "models" / "x2_headless.xml"))


def test_forward_policy_has_measurable_displacement():
    metrics = _simulator().execute("move_forward", 0.5, {"distance": 0.2})
    assert metrics["root_displacement"] > 0.01
    assert metrics["state_delta"] > 0.01


def test_wave_policy_changes_joint_state():
    metrics = _simulator().execute("wave_arm", 0.5, {})
    assert metrics["state_delta"] > 1e-5


def test_unknown_policy_fails_closed():
    try:
        _simulator().execute("unsafe_unknown", 0.1, {})
    except ValueError as exc:
        assert "unsupported" in str(exc)
    else:
        raise AssertionError("unknown policy was accepted")
