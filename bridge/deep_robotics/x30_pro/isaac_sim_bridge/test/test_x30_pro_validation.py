"""Validation tests for Deep Robotics X30 Pro mapper velocity constraints.

Verifies that velocity commands respect X30 Pro constraints:
  - vx ∈ [0, 1.5] m/s (forward), vx ∈ [-1.5, 0] m/s (backward)
  - wz ∈ [-1.0, 1.0] rad/s

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

# --- Direct import of mapper module (bypass x30_pro/__init__.py) ---
_mapper_path = os.path.join(os.path.dirname(__file__), "..", "x30_pro", "mapper.py")
_spec = importlib.util.spec_from_file_location("x30_pro_mapper", _mapper_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
X30ProMapper = _mod.X30ProMapper


def test_forward_within_bounds():
    mapper = X30ProMapper(forward_speed=1.0)
    twist = mapper.map(_ActionEvent("move_forward"))
    assert 0.0 <= twist.linear.x <= 1.5, f"vx={twist.linear.x} out of [0, 1.5]"
    assert twist.angular.z == 0.0


def test_backward_within_bounds():
    mapper = X30ProMapper(backward_speed=0.5)
    twist = mapper.map(_ActionEvent("move_backward"))
    assert -1.5 <= twist.linear.x <= 0.0, f"vx={twist.linear.x} out of [-1.5, 0]"
    assert twist.angular.z == 0.0


def test_turn_left_within_bounds():
    mapper = X30ProMapper(turn_angular_speed=0.8)
    twist = mapper.map(_ActionEvent("turn_left"))
    assert twist.linear.x == 0.0
    assert 0.0 < twist.angular.z <= 1.0, f"wz={twist.angular.z} out of (0, 1.0]"


def test_turn_right_within_bounds():
    mapper = X30ProMapper(turn_angular_speed=0.8)
    twist = mapper.map(_ActionEvent("turn_right"))
    assert twist.linear.x == 0.0
    assert -1.0 <= twist.angular.z < 0.0, f"wz={twist.angular.z} out of [-1.0, 0)"


def test_stop_produces_zero_twist():
    mapper = X30ProMapper()
    twist = mapper.map(_ActionEvent("stop"))
    assert twist.linear.x == 0.0
    assert twist.angular.z == 0.0


def test_unknown_action_produces_zero_twist():
    mapper = X30ProMapper()
    twist = mapper.map(_ActionEvent("dance"))
    assert twist.linear.x == 0.0
    assert twist.angular.z == 0.0


def test_forward_speed_clamped_at_max():
    mapper = X30ProMapper(forward_speed=999.0)
    twist = mapper.map(_ActionEvent("move_forward"))
    assert twist.linear.x == 1.5, f"Expected clamp to 1.5, got {twist.linear.x}"


def test_backward_speed_clamped_at_max():
    mapper = X30ProMapper(backward_speed=999.0)
    twist = mapper.map(_ActionEvent("move_backward"))
    assert twist.linear.x == -1.5, f"Expected clamp to -1.5, got {twist.linear.x}"


def test_turn_angular_speed_clamped_at_max():
    mapper = X30ProMapper(turn_angular_speed=999.0)
    twist = mapper.map(_ActionEvent("turn_left"))
    assert twist.angular.z == 1.0, f"Expected clamp to 1.0, got {twist.angular.z}"


def test_all_actions_vx_in_bounds():
    """X30 Pro vx must always be in [-1.5, 1.5]."""
    mapper = X30ProMapper(forward_speed=1.5, backward_speed=1.5, turn_angular_speed=1.0)
    for action in ("move_forward", "forward", "move_backward", "backward",
                    "turn_left", "turn_right", "stop", "unknown_action"):
        twist = mapper.map(_ActionEvent(action))
        assert -1.5 <= twist.linear.x <= 1.5, f"action={action}: vx={twist.linear.x} out of bounds"


def test_all_actions_wz_in_bounds():
    """X30 Pro wz must always be in [-1.0, 1.0]."""
    mapper = X30ProMapper(forward_speed=1.5, turn_angular_speed=1.0)
    for action in ("move_forward", "move_backward", "turn_left",
                    "turn_right", "stop", "unknown"):
        twist = mapper.map(_ActionEvent(action))
        assert -1.0 <= twist.angular.z <= 1.0, \
            f"action={action}: wz={twist.angular.z} out of bounds"


def test_quadruped_can_walk_backward():
    """X30 Pro is a quadruped — must support negative vx for backward."""
    mapper = X30ProMapper(backward_speed=1.0)
    twist = mapper.map(_ActionEvent("move_backward"))
    assert twist.linear.x < 0.0, f"Quadruped backward: vx={twist.linear.x} should be < 0"


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
