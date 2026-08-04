"""
Executable contract tests for the M20 Pro RoboPay bridge.

These tests use a fake runner (no real MuJoCo dependency needed to run them)
to validate the fail-closed routing, replay protection, and settlement-gate
behavior described in execution-mapping.yaml. Physical-motion-equivalent
(simulator state change) is validated separately via the real runner in
docs/validation-report.md.
"""

import time

from bridge.m20_pro_zenoh_bridge import (
    M20ProRoboPayBridge,
    RejectReason,
    ReplayStore,
    canonical_params_hash,
)

ROBOT_ID = "deep-robotics-m20-pro-sim-01"
SKILL_ID = "m20_pro_obstacle_navigation"


class FakeRunner:
    def __init__(self, next_result=None):
        self.next_result = next_result or {
            "status": "goal_reached",
            "displacement_m": 8.0,
            "path_length_m": 8.5,
            "collisions": 0,
            "target_distance_remaining_m": 0.1,
            "sim_steps": 4000,
            "sim_seconds": 8.0,
        }
        self.calls = 0
        self.stopped = False

    def run_episode(self, params):
        self.calls += 1
        return self.next_result

    def stop(self):
        self.stopped = True


def make_action(**overrides):
    params = {"target_xy": [8.0, 0.0], "max_episode_steps": 50000}
    base = {
        "actionId": "action-1",
        "robotId": ROBOT_ID,
        "skillId": SKILL_ID,
        "params": params,
        "paramsHash": canonical_params_hash(params),
        "idempotencyKey": "idem-1",
        "payment": {
            "status": "verified",
            "verified": True,
            "authorizationId": "auth-1",
            "expiresAt": time.time() + 300,
        },
    }
    base.update(overrides)
    return base


def test_successful_action_settles():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    result = bridge.handle_raw_action(make_action())
    assert result.status == "success"
    assert result.settlementEligible is True
    assert runner.calls == 1


def test_unpaid_action_is_rejected_before_actuation():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    action = make_action(payment={"status": "unverified", "verified": False})
    result = bridge.handle_raw_action(action)
    assert result.status == "rejected"
    assert result.reason == RejectReason.PAYMENT_UNVERIFIED
    assert result.settlementEligible is False
    assert runner.calls == 0  # simulator never touched


def test_invalid_params_hash_is_rejected():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    action = make_action(paramsHash="deadbeef")
    result = bridge.handle_raw_action(action)
    assert result.status == "rejected"
    assert result.reason == RejectReason.BAD_PARAMS_HASH
    assert runner.calls == 0


def test_expired_payment_is_rejected():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    action = make_action(
        payment={
            "status": "verified",
            "verified": True,
            "authorizationId": "auth-1",
            "expiresAt": time.time() - 10,
        }
    )
    result = bridge.handle_raw_action(action)
    assert result.status == "rejected"
    assert result.reason == RejectReason.PAYMENT_EXPIRED
    assert runner.calls == 0


def test_malformed_envelope_is_rejected():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    result = bridge.handle_raw_action({"actionId": "x"})
    assert result.status == "rejected"
    assert result.reason == RejectReason.MALFORMED
    assert runner.calls == 0


def test_replay_of_same_action_id_causes_no_second_motion():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    action = make_action()

    first = bridge.handle_raw_action(action)
    assert first.status == "success"
    assert runner.calls == 1

    replay = bridge.handle_raw_action(action)  # identical actionId
    assert replay.status == "rejected"
    assert replay.reason == RejectReason.DUPLICATE
    assert replay.settlementEligible is False
    assert runner.calls == 1  # no second motion


def test_collision_result_is_never_settlement_eligible():
    runner = FakeRunner(next_result={
        "status": "collision",
        "displacement_m": 2.0,
        "path_length_m": 2.4,
        "collisions": 3,
        "target_distance_remaining_m": 6.0,
        "sim_steps": 1200,
        "sim_seconds": 2.4,
    })
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    result = bridge.handle_raw_action(make_action())
    assert result.status == "error"
    assert result.settlementEligible is False
    assert result.reason == "episode_status:collision"


def test_timeout_result_is_never_settlement_eligible():
    runner = FakeRunner(next_result={
        "status": "timeout",
        "displacement_m": 5.0,
        "path_length_m": 6.0,
        "collisions": 0,
        "target_distance_remaining_m": 3.0,
        "sim_steps": 50000,
        "sim_seconds": 100.0,
    })
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    result = bridge.handle_raw_action(make_action())
    assert result.status == "error"
    assert result.settlementEligible is False
    assert result.reason == "episode_status:timeout"


def test_stop_requires_no_payment_and_zeroes_velocity():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    result = bridge.handle_stop("stop-action-1")
    assert result.status == "success"
    assert result.settlementEligible is False
    assert runner.stopped is True


def test_wrong_robot_id_is_rejected():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    action = make_action(robotId="some-other-robot")
    result = bridge.handle_raw_action(action)
    assert result.status == "rejected"
    assert result.reason == RejectReason.WRONG_ROBOT
    assert runner.calls == 0


def test_unknown_skill_id_is_rejected():
    runner = FakeRunner()
    bridge = M20ProRoboPayBridge(ROBOT_ID, runner, ReplayStore())
    action = make_action(skillId="not_a_real_skill")
    result = bridge.handle_raw_action(action)
    assert result.status == "rejected"
    assert result.reason == RejectReason.UNKNOWN_SKILL
    assert runner.calls == 0
