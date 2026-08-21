"""Measured-state right-arm wave controller for Webots' built-in Atlas DRC."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from controller import Supervisor


TIME_STEP = 16
CYCLES = 2
AMPLITUDE_RAD = 0.30
MAX_DURATION_SEC = 8.0
TARGET_TOLERANCE_RAD = 0.06
SETTLED_STEPS_REQUIRED = 6
RESULT_PATH = Path(__file__).resolve().parents[2] / "scenes" / "webots_wave_result.json"


def _joint_position_field(atlas, definition: str):
    """Read the actual HingeJoint position through the Supervisor API."""

    # The joint DEFs are internal to Webots' Atlas PROTO. A world-level
    # ``getFromDef`` cannot see them; the public PROTO-node API can.
    joint = atlas.getFromProtoDef(definition)
    if joint is None:
        raise RuntimeError(f"Missing Webots Atlas joint DEF {definition}")
    parameters = joint.getField("jointParameters").getSFNode()
    if parameters is None:
        raise RuntimeError(f"Missing HingeJointParameters for {definition}")
    field = parameters.getField("position")
    if field is None:
        raise RuntimeError(f"Webots did not expose measured position for {definition}")
    return field


def main() -> None:
    robot = Supervisor()
    atlas = robot.getFromDef("ATLAS_DRC")
    if atlas is None:
        raise RuntimeError("World must expose the Atlas instance as DEF ATLAS_DRC")
    shoulder_position = _joint_position_field(atlas, "RArmUsy")
    shoulder = robot.getDevice("RArmUsy")
    shoulder_roll = robot.getDevice("RArmShx")
    elbow = robot.getDevice("RArmEly")
    elbow_roll = robot.getDevice("RArmElx")
    wrist = robot.getDevice("RArmUwy")
    for motor, target in ((shoulder_roll, -0.25), (elbow, 1.05), (elbow_roll, -0.75), (wrist, 0.15)):
        motor.setPosition(target)
        motor.setVelocity(2.0)
    shoulder.setVelocity(1.5)

    recording = os.environ.get("ATLAS_WEBOTS_RECORDING_PATH", "").strip()
    if recording:
        Path(recording).parent.mkdir(parents=True, exist_ok=True)
        # MPEG-4, high quality, real-time acceleration. This is opt-in so the
        # normal CI validation remains fast and headless.
        robot.movieStartRecording(recording, 1280, 720, 0, 100, 1, False)

    phase = 0
    settled_steps = 0
    values: list[float] = []
    root_heights: list[float] = []
    upright_cosines: list[float] = []
    start_time = robot.getTime()
    while robot.step(TIME_STEP) != -1 and robot.getTime() - start_time < MAX_DURATION_SEC:
        position = float(shoulder_position.getSFFloat())
        values.append(position)
        root_heights.append(float(atlas.getPosition()[2]))
        # Third column of the local-to-world rotation matrix is the Atlas
        # torso's local +Z axis expressed in world coordinates.  Its world-Z
        # component is one while upright and approaches zero as it falls.
        upright_cosines.append(float(atlas.getOrientation()[8]))
        target = AMPLITUDE_RAD if phase % 2 == 0 else -AMPLITUDE_RAD
        shoulder.setPosition(target)
        if abs(position - target) <= TARGET_TOLERANCE_RAD:
            settled_steps += 1
        else:
            settled_steps = 0
        if settled_steps >= SETTLED_STEPS_REQUIRED:
            phase += 1
            settled_steps = 0
            if phase >= CYCLES * 2:
                break

    stroke = (max(values) - min(values)) if values else 0.0
    minimum_root_height = min(root_heights) if root_heights else 0.0
    minimum_upright_cosine = min(upright_cosines) if upright_cosines else -1.0
    recording_ready = True
    if recording:
        robot.movieStopRecording()
        deadline = robot.getTime() + 15.0
        while not robot.movieIsReady() and not robot.movieFailed() and robot.getTime() < deadline:
            if robot.step(TIME_STEP) == -1:
                break
        recording_ready = robot.movieIsReady() and not robot.movieFailed() and Path(recording).is_file()

    stable_base = minimum_root_height >= 0.85 and minimum_upright_cosine >= 0.90
    success = (
        phase >= CYCLES * 2
        and stroke >= AMPLITUDE_RAD * 1.35
        and stable_base
        and recording_ready
    )
    result = {
        "simulator_engine": "Webots",
        "robot_model": "Boston Dynamics Atlas DRC legacy (Webots R2025a built-in PROTO)",
        "task": "wave_right_arm",
        "status": "success" if success else "failure",
        "success": success,
        "controller": "state_feedback_target_switching_motor_controller",
        "policy_id": "atlas-drc-right-arm-wave-v1",
        "requested_cycles": CYCLES,
        "completed_half_waves": phase,
        "requested_amplitude_rad": AMPLITUDE_RAD,
        "measured_wave_stroke_rad": round(stroke, 5),
        "minimum_root_height_m": round(minimum_root_height, 5),
        "minimum_upright_cosine": round(minimum_upright_cosine, 5),
        "stable_base": stable_base,
        "sim_duration_seconds": round(robot.getTime() - start_time, 3),
        "state_authority": "RArmUsy HingeJointParameters.position via Supervisor",
    }
    if recording:
        result["visual_recording"] = recording
        result["visual_recording_ready"] = recording_ready
    RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    # Keep the completed pose visible only when an operator explicitly asks
    # for a graphical preview or recording.  Automated Sim-to-Sim runs leave
    # this unset and still terminate immediately after producing the result.
    hold_seconds = max(0.0, float(os.environ.get("ATLAS_WEBOTS_HOLD_SECONDS", "0")))
    hold_until = time.monotonic() + hold_seconds
    while hold_seconds and time.monotonic() < hold_until:
        if robot.step(TIME_STEP) == -1:
            break
        time.sleep(TIME_STEP / 1000.0)
    robot.simulationQuit(0 if success else 1)


if __name__ == "__main__":
    main()
