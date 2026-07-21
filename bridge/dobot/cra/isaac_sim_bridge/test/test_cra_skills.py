"""Validation tests for Dobot CRA obstacle navigation skill.

Verifies that the obstacle navigation policy respects CRA velocity constraints:
  - vx ∈ [0, 0.5] m/s
  - wz ∈ [-0.3, 0.3] rad/s
"""
import sys
import os
import types
import importlib.util
import math

# --- Lightweight stubs ---
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

# Stub geometry_msgs
geometry_msgs = types.ModuleType("geometry_msgs")
geometry_msgs_msg = types.ModuleType("geometry_msgs.msg")


class _Twist:
    def __init__(self):
        self.linear = types.SimpleNamespace(x=0.0)
        self.angular = types.SimpleNamespace(z=0.0)


geometry_msgs_msg.Twist = _Twist
geometry_msgs.msg = geometry_msgs_msg
sys.modules["geometry_msgs"] = geometry_msgs
sys.modules["geometry_msgs.msg"] = geometry_msgs_msg

# --- Direct imports of skill modules ---
_base_path = os.path.join(os.path.dirname(__file__), "..", "cra", "skills", "base.py")
_spec_base = importlib.util.spec_from_file_location("cra_skills_base", _base_path)
_mod_base = importlib.util.module_from_spec(_spec_base)
_spec_base.loader.exec_module(_mod_base)

_nav_path = os.path.join(os.path.dirname(__file__), "..", "cra", "skills", "obstacle_nav.py")
_spec_nav = importlib.util.spec_from_file_location("cra_skills_obstacle_nav", _nav_path)
_mod_nav = importlib.util.module_from_spec(_spec_nav)
# Patch the base module into the nav module's namespace
_mod_nav.__package__ = "cra.skills"
sys.modules["cra.skills.base"] = _mod_base
_spec_nav.loader.exec_module(_mod_nav)

ObstacleNavSkill = _mod_nav.ObstacleNavSkill


def _make_skill_and_navigate(goal=(5.0, 0.0), obstacles=None, heading=0.0):
    """Run the obstacle nav skill for a few steps and collect velocity commands."""
    skill = ObstacleNavSkill(max_speed=0.5, max_angular=0.3)
    skill.reset({"goal": goal, "start": (0.0, 0.0), "obstacles": obstacles or []})

    commands = []
    pos = [0.0, 0.0]
    for _ in range(20):
        result = skill.step({"position": tuple(pos), "heading": heading})
        commands.append(result)
        if result.status in ("success", "failed"):
            break
        # Simulate simple motion: advance position by result speed
        pos[0] += result.speed * 0.1
        pos[1] += result.angular_speed * 0.1
    return commands


def test_obstacle_nav_vx_always_non_negative():
    """All navigation commands must have vx >= 0."""
    commands = _make_skill_and_navigate(goal=(5.0, 0.0))
    for i, cmd in enumerate(commands):
        assert cmd.speed >= 0.0, f"step {i}: speed={cmd.speed} < 0"


def test_obstacle_nav_vx_within_max():
    """All navigation commands must have vx <= 0.5 m/s."""
    commands = _make_skill_and_navigate(goal=(5.0, 0.0))
    for i, cmd in enumerate(commands):
        assert cmd.speed <= 0.5, f"step {i}: speed={cmd.speed} > 0.5"


def test_obstacle_nav_wz_within_bounds():
    """All navigation commands must have wz in [-0.3, 0.3]."""
    commands = _make_skill_and_navigate(goal=(3.0, 3.0))
    for i, cmd in enumerate(commands):
        assert -0.3 <= cmd.angular_speed <= 0.3, \
            f"step {i}: angular_speed={cmd.angular_speed} out of [-0.3, 0.3]"


def test_obstacle_nav_goal_reached():
    """Skill should report success when near goal."""
    skill = ObstacleNavSkill(goal_threshold=0.5)
    skill.reset({"goal": (0.1, 0.0), "start": (0.0, 0.0), "obstacles": []})
    result = skill.step({"position": (0.05, 0.0), "heading": 0.0})
    assert result.status == "success", f"Expected success, got {result.status}"


def test_obstacle_nav_avoids_obstacle():
    """Skill should still complete even with obstacles."""
    commands = _make_skill_and_navigate(
        goal=(3.0, 0.0), obstacles=[(1.5, 0.0)], heading=0.0
    )
    # Should have commands and not crash
    assert len(commands) > 0
    # All commands should still respect bounds
    for i, cmd in enumerate(commands):
        assert 0.0 <= cmd.speed <= 0.5, f"step {i}: speed={cmd.speed}"
        assert -0.3 <= cmd.angular_speed <= 0.3, f"step {i}: wz={cmd.angular_speed}"


def test_obstacle_nav_timeout():
    """Skill should timeout after 1000 steps."""
    skill = ObstacleNavSkill(max_speed=0.01)  # very slow
    skill.reset({"goal": (1000.0, 1000.0), "start": (0.0, 0.0), "obstacles": []})
    for _ in range(1001):
        result = skill.step({"position": (0.0, 0.0), "heading": 0.0})
    assert result.status == "failed", f"Expected timeout/failed, got {result.status}"


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
