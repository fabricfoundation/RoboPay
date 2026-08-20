"""Webots adapter for the shared Booster K1 inspection policy."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from controller import Supervisor


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PACKAGE_ROOT))

from k1_inspection_bridge.control_core import K1InspectionControlCore, POLICY_ID  # noqa: E402


JOINT_NAMES = (
    "AAHead_yaw", "Head_pitch", "ALeft_Shoulder_Pitch", "Left_Shoulder_Roll",
    "Left_Elbow_Pitch", "Left_Elbow_Yaw", "ARight_Shoulder_Pitch", "Right_Shoulder_Roll",
    "Right_Elbow_Pitch", "Right_Elbow_Yaw", "Left_Hip_Pitch", "Left_Hip_Roll",
    "Left_Hip_Yaw", "Left_Knee_Pitch", "Left_Ankle_Pitch", "Left_Ankle_Roll",
    "Right_Hip_Pitch", "Right_Hip_Roll", "Right_Hip_Yaw", "Right_Knee_Pitch",
    "Right_Ankle_Pitch", "Right_Ankle_Roll",
)
RESULT = PACKAGE_ROOT / "scenes" / "webots_inspection_result.json"


def main() -> None:
    robot = Supervisor()
    timestep = int(robot.getBasicTimeStep())
    motors = [robot.getDevice(name) for name in JOINT_NAMES]
    sensors = []
    for motor in motors:
        motor.setVelocity(min(3.0, motor.getMaxVelocity()))
        sensor = motor.getPositionSensor()
        sensor.enable(timestep)
        sensors.append(sensor)

    neutral = [0.0] * 22
    neutral[3] = -1.30
    neutral[7] = 1.30
    for motor, value in zip(motors, neutral, strict=True):
        motor.setPosition(value)

    policy = K1InspectionControlCore(("left", "center", "right"), 1.0)
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
        plan = policy.compute_plan({"sim_time": robot.getTime(), "upper_joint_positions": measured[:10]})
        latest = policy.diagnostics(plan)
        target = neutral[:]
        if plan.target is not None:
            target[:10] = plan.commanded_upper_joints
        for motor, value in zip(motors, target, strict=True):
            motor.setPosition(value)
        steps += 1
        if policy.phase == "COMPLETE":
            break

    success = policy.phase == "COMPLETE" and policy.completed_targets == ["left", "center", "right"]
    result = {
        "simulator_engine": "Webots",
        "simulator_version": "R2025a",
        "robot_model": "Booster Robotics K1 22-DoF (official URDF conversion)",
        "model_source_commit": "508cbee6ca9ae6fbc8c0b38dd58785a6f3fc61a2",
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
        "head_final_yaw_rad": round(previous[0] if previous is not None else 0.0, 4),
        "head_final_pitch_rad": round(previous[1] if previous is not None else 0.0, 4),
        "max_joint_speed_rad_s": round(max_speed, 4),
        "support_fixture": "fixed-base safety stand",
        "controller": "shared_closed_loop_target_sequencer_with_webots_position_adapter",
        "policy_id": POLICY_ID,
        "policy_state": latest,
    }
    RESULT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if os.environ.get("BOOSTER_K1_WEBOTS_HOLD_VIEWER", "") != "1":
        robot.simulationQuit(0 if success else 1)


if __name__ == "__main__":
    main()
