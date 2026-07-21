"""Validation tests for Dobot CRA mapper velocity constraints.

Verifies that velocity commands respect CRA constraints:
  - vx ∈ [0, 0.5] m/s
  - wz ∈ [-0.3, 0.3] rad/s

Runs standalone without ROS2 by providing lightweight stubs.
"""
import sys
import os
import types
import importlib.util

# --- Lightweight stubs so mapper.py can be imported without ROS2 ---
# Stub geometry_msgs.msg.Twist
geometry_msgs = types.ModuleType("geometry_msgs")
geometry_msgs_msg = types.ModuleType("geometry_msgs.msg")


class _Twist:
    """Minimal Twist stub with linear.x and angular.z."""
    def __init__(self):
        self.linear = types.SimpleNamespace(x=0.0)
        self.angular = types.SimpleNamespace(z=0.0)


geometry_msgs_msg.Twist = _Twist
geometry_msgs.msg = geometry_msgs_msg
sys.modules["geometry_msgs"] = geometry_msgs
sys.modules["geometry_msgs.msg"] = geometry_msgs_msg

# Stub zenoh_bridge (ActionEvent, CommandMapper, clamp)
zenoh_bridge = types.ModuleType("zenoh_bridge")


class _ActionEvent:
    def __init__(self, action, params=None):
        self.action = action
        self.params = params or {}


class _CommandMapper:
    pass


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


zenoh_bridge.ActionEvent = _ActionEvent
zenoh_bridge.CommandMapper = _CommandMapper
zenoh_bridge.clamp = _clamp
sys.modules["zenoh_bridge"] = zenoh_bridge

# --- Direct import of mapper module (bypass cra/__init__.py) ---
_mapper_path = os.path.join(os.path.dirname(__file__), "..", "cra", "mapper.py")
_spec = importlib.util.spec_from_file_location("cra_mapper", _mapper_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
CraMapper = _mod.CraMapper


def test_forward_within_bounds():
    mapper = CraMapper(forward_speed=0.3)
    twist = mapper.map(_ActionEvent("move_forward"))
    assert 0.0 <= twist.linear.x <= 0.5, f"vx={twist.linear.x} out of [0, 0.5]"
    assert twist.angular.z == 0.0


def test_backward_clamped_to_zero():
    mapper = CraMapper(backward_speed=0.3)
    twist = mapper.map(_ActionEvent("move_backward"))
    assert twist.linear.x == 0.0, "CRA cannot reverse — expected 0.0"


def test_turn_left_within_bounds():
    mapper = CraMapper(turn_linear_speed=0.1, turn_angular_speed=0.2)
    twist = mapper.map(_ActionEvent("turn_left"))
    assert 0.0 <= twist.linear.x <= 0.5, f"vx={twist.linear.x} out of [0, 0.5]"
    assert 0.0 < twist.angular.z <= 0.3, f"wz={twist.angular.z} out of (0, 0.3]"


def test_turn_right_within_bounds():
    mapper = CraMapper(turn_linear_speed=0.1, turn_angular_speed=0.2)
    twist = mapper.map(_ActionEvent("turn_right"))
    assert 0.0 <= twist.linear.x <= 0.5, f"vx={twist.linear.x} out of [0, 0.5]"
    assert -0.3 <= twist.angular.z < 0.0, f"wz={twist.angular.z} out of [-0.3, 0)"


def test_stop_produces_zero_twist():
    mapper = CraMapper()
    twist = mapper.map(_ActionEvent("stop"))
    assert twist.linear.x == 0.0
    assert twist.angular.z == 0.0


def test_unknown_action_produces_zero_twist():
    mapper = CraMapper()
    twist = mapper.map(_ActionEvent("dance"))
    assert twist.linear.x == 0.0
    assert twist.angular.z == 0.0


def test_forward_speed_clamped_at_max():
    mapper = CraMapper(forward_speed=999.0)
    twist = mapper.map(_ActionEvent("move_forward"))
    assert twist.linear.x == 0.5, f"Expected clamp to 0.5, got {twist.linear.x}"


def test_turn_angular_speed_clamped_at_max():
    mapper = CraMapper(turn_angular_speed=999.0)
    twist = mapper.map(_ActionEvent("turn_left"))
    assert twist.angular.z == 0.3, f"Expected clamp to 0.3, got {twist.angular.z}"


def test_all_actions_vx_non_negative():
    """CRA vx must always be >= 0."""
    mapper = CraMapper(forward_speed=0.5, backward_speed=0.5, turn_angular_speed=0.3)
    for action in ("move_forward", "forward", "move_backward", "backward",
                    "turn_left", "turn_right", "stop", "unknown_action"):
        twist = mapper.map(_ActionEvent(action))
        assert twist.linear.x >= 0.0, f"action={action}: vx={twist.linear.x} < 0"


def test_all_actions_wz_in_bounds():
    """CRA wz must always be in [-0.3, 0.3]."""
    mapper = CraMapper(forward_speed=0.5, turn_angular_speed=0.3)
    for action in ("move_forward", "move_backward", "turn_left",
                    "turn_right", "stop", "unknown"):
        twist = mapper.map(_ActionEvent(action))
        assert -0.3 <= twist.angular.z <= 0.3, \
            f"action={action}: wz={twist.angular.z} out of bounds"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests")
    sys.exit(1 if failed else 0)
