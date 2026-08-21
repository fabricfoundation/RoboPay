"""Webots adapter for the shared Unitree G1 inspection policy."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from controller import Supervisor


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))

from g1_inspection_bridge.control_core import G1InspectionControlCore, POLICY_ID  # noqa: E402


MODEL_COMMIT = "daadf41ee9afce8f90fdc09a98506012691fa122"
JOINT_NAMES = (
    "left_hip_pitch_joint", "left_hip_roll_joint", "left_hip_yaw_joint", "left_knee_joint",
    "left_ankle_pitch_joint", "left_ankle_roll_joint", "right_hip_pitch_joint", "right_hip_roll_joint",
    "right_hip_yaw_joint", "right_knee_joint", "right_ankle_pitch_joint", "right_ankle_roll_joint",
    "waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint", "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint", "left_shoulder_yaw_joint", "left_elbow_joint", "left_wrist_roll_joint",
    "left_wrist_pitch_joint", "left_wrist_yaw_joint", "right_shoulder_pitch_joint", "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint", "right_elbow_joint", "right_wrist_roll_joint", "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)
INSPECTION_INDICES = (12, 13, 14, 15, 16, 18, 20, 22, 23, 25, 27)
RESULT = PACKAGE_ROOT / "scenes" / "webots_inspection_result.json"


def main() -> None:
    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())
    viewer_mode = os.environ.get("UNITREE_G1_WEBOTS_HOLD_VIEWER", "") == "1"
    start_hold_seconds = float(os.environ.get("UNITREE_G1_WEBOTS_START_HOLD_SECONDS", "0")) if viewer_mode else 0.0
    target_hold_seconds = float(os.environ.get("UNITREE_G1_WEBOTS_TARGET_HOLD_SECONDS", "0")) if viewer_mode else 0.0
    motors = [robot.getDevice(name) for name in JOINT_NAMES]
    sensors = []
    for motor in motors:
        motor.setVelocity(min(2.2, motor.getMaxVelocity()))
        sensor = motor.getPositionSensor()
        sensor.enable(timestep)
        sensors.append(sensor)

    neutral = [0.0] * 29
    neutral[18] = 0.25
    neutral[25] = 0.25
    for motor, value in zip(motors, neutral, strict=True):
        motor.setPosition(value)

    start_deadline = robot.getTime() + start_hold_seconds
    while robot.getTime() < start_deadline and robot.step(timestep) != -1:
        pass

    policy = G1InspectionControlCore(("left", "center", "right"), 1.0)
    policy.reset()
    latest = {}
    max_speed = 0.0
    previous = None
    steps = 0
    while robot.step(timestep) != -1 and robot.getTime() < 18.0:
        measured = [sensor.getValue() for sensor in sensors]
        dt = timestep / 1000.0
        if previous is not None:
            max_speed = max(max_speed, max(abs(now - before) / dt for now, before in zip(measured, previous, strict=True)))
        previous = measured
        inspection = [measured[index] for index in INSPECTION_INDICES]
        confirmed_before_step = len(policy.completed_targets)
        plan = policy.compute_plan({"sim_time": robot.getTime(), "inspection_joint_positions": inspection})
        latest = policy.diagnostics(plan)
        target = neutral[:]
        if plan.target is not None:
            for index, value in zip(INSPECTION_INDICES, plan.commanded_inspection_joints, strict=True):
                target[index] = value
        for motor, value in zip(motors, target, strict=True):
            motor.setPosition(value)
        steps += 1
        if len(policy.completed_targets) > confirmed_before_step and target_hold_seconds > 0:
            hold_deadline = robot.getTime() + target_hold_seconds
            while robot.getTime() < hold_deadline and robot.step(timestep) != -1:
                steps += 1
        if policy.phase == "COMPLETE":
            break

    success = policy.phase == "COMPLETE" and policy.completed_targets == ["left", "center", "right"]
    result = {
        "simulator_engine": "Webots",
        "simulator_version": "R2025a",
        "robot_model": "Unitree G1 29-DoF (official unitree_ros URDF conversion)",
        "model_source_commit": MODEL_COMMIT,
        "task": "inspect_target_sequence",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": "targets_confirmed" if success else "time_limit",
        "safe_stop_applied": False,
        "sim_duration_seconds": round(robot.getTime(), 3),
        "control_steps": steps,
        "targets_requested": ["left", "center", "right"],
        "targets_confirmed": list(policy.completed_targets),
        "target_confirmations": list(policy.target_confirmations),
        "waist_final_yaw_rad": round(previous[12] if previous is not None else 0.0, 4),
        "waist_final_pitch_rad": round(previous[14] if previous is not None else 0.0, 4),
        "max_joint_speed_rad_s": round(max_speed, 4),
        "support_fixture": "pelvis safety fixture with feet on floor",
        "controller": "shared_closed_loop_target_sequencer_with_webots_position_adapter",
        "policy_id": POLICY_ID,
        "policy_state": latest,
    }
    RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if viewer_mode:
        while robot.step(timestep) != -1:
            pass
    else:
        robot.simulationQuit(0 if success else 1)


if __name__ == "__main__":
    main()
