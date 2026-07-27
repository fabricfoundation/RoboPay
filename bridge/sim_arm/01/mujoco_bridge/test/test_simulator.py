"""Simulator tests — no ROS2 dependency, runs in CI with just mujoco + pytest."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sim_arm_01.simulator import SimArm01Simulator


def test_reaches_target():
    sim = SimArm01Simulator()
    metrics = sim.execute([1.0, -0.5])
    assert metrics["success"] is True
    assert metrics["joint_error"] < 0.03
    assert metrics["collision"] is False


def test_unreachable_target_fails():
    # [5.0, 5.0] is outside the reachable +/-3.14 rad joint range. The actuator
    # drives to its limit (~3.14) but physically cannot reach 5.0, so the arm
    # genuinely fails to converge. This must be reported as a real failure.
    sim = SimArm01Simulator()
    metrics = sim.execute([5.0, 5.0])
    assert metrics["success"] is False, "unreachable target must fail, not succeed"
    assert metrics["joint_error"] > 1.0, f"error should be large: {metrics}"
    assert metrics["steps_taken"] == 1200, "should exhaust the step budget without settling"
