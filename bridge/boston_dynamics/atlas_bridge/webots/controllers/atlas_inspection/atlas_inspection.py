"""Webots controller for the Atlas shelf-inspection skill.

Runs inside Webots and drives the robot with the *same*
:class:`~control_core.ShelfInspectionController` and the *same*
URDF kinematics that the MuJoCo and PyBullet backends use.  Only the physics
engine and the joint servo differ.

The result is written as JSON to ``$ATLAS_WEBOTS_RESULT`` so the launcher in
``webots_env.py`` can report it without re-deriving anything.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

# The controller runs as its own process, so put the repository on the path.
REPOSITORY_ROOT = Path(__file__).resolve().parents[6]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from bridge.boston_dynamics.atlas_bridge import kinematics  # noqa: E402
from bridge.boston_dynamics.atlas_bridge.control_core import (  # noqa: E402
    POLICY_ID,
    ShelfInspectionController,
)
from bridge.boston_dynamics.atlas_bridge.episode import MODEL_SOURCE, REQUIRED_TARGETS  # noqa: E402
from bridge.boston_dynamics.atlas_bridge.model import joint_efforts  # noqa: E402
from bridge.boston_dynamics.atlas_bridge.task import (  # noqa: E402
    END_EFFECTOR_BODY,
    EPISODE_BUDGET_S,
    FALL_THRESHOLD_M,
    SHELF_PARTS,
    STANCE_POSE,
)

from bridge.boston_dynamics.atlas_bridge.task import WEBOTS_SERVO_P  # noqa: E402

from controller import Supervisor  # noqa: E402  (provided by Webots at runtime)


class WebotsInspectionEnvironment:
    """Thin Webots adapter exposing the same surface as the other backends."""

    def __init__(self) -> None:
        self.robot = Supervisor()
        self.timestep = int(self.robot.getBasicTimeStep())
        self.names = tuple(joint_efforts())

        self.motors = {}
        self.sensors = {}
        for name in self.names:
            motor = self.robot.getDevice(name)
            sensor = self.robot.getDevice(f"{name}_sensor")
            if motor is None or sensor is None:
                raise RuntimeError(f"Webots PROTO is missing device for joint {name}")
            sensor.enable(self.timestep)
            # Webots' position servo is an implicit velocity-level controller.
            # Its default gain of P=10 tracks too slowly to hold a 182 kg
            # humanoid: the ankle lags, the torso pitches and Atlas topples after
            # about a second.  P=120 holds the stance at 0.911 m, matching the
            # other two engines.  Torque stays clamped to the URDF effort limit
            # by the motor itself.
            motor.setControlPID(WEBOTS_SERVO_P, 0.0, 0.0)
            self.motors[name] = motor
            self.sensors[name] = sensor

        self.self_node = self.robot.getSelf()
        # DEF nodes that live inside a PROTO are not reachable through the
        # world-level getFromDef; they have to be resolved against the PROTO
        # instance itself.
        self.hand_node = self.self_node.getFromProtoDef(END_EFFECTOR_BODY)
        if self.hand_node is None:
            self.hand_node = self.robot.getFromDef(END_EFFECTOR_BODY)
        if self.hand_node is None:
            raise RuntimeError(f"Webots PROTO exposes no DEF {END_EFFECTOR_BODY}")
        self.shelf_nodes = [
            self.robot.getFromDef(part["name"]) for part in SHELF_PARTS
        ]
        missing = [
            part["name"] for part, node in zip(SHELF_PARTS, self.shelf_nodes) if node is None
        ]
        if missing:
            raise RuntimeError(f"World is missing shelf solids: {missing}")
        for node in self.shelf_nodes:
            node.enableContactPointsTracking(self.timestep)

        self.min_pelvis_height = math.inf
        self.max_end_effector_speed = 0.0
        self.shelf_contacts = 0
        self.fall_detected = False
        self._previous_hand = None

    # -- episode -----------------------------------------------------------
    def joint_limits(self) -> dict[str, tuple[float, float]]:
        limits = {}
        for name, motor in self.motors.items():
            low, high = motor.getMinPosition(), motor.getMaxPosition()
            limits[name] = (low, high) if low < high else (-math.pi, math.pi)
        return limits

    def reset(self, joint_targets: dict[str, float]) -> dict:
        pose = {**STANCE_POSE, **joint_targets}
        for name, motor in self.motors.items():
            motor.setPosition(float(pose.get(name, 0.0)))
        # One warm-up step so the position sensors return real values instead of
        # NaN on the first read.
        self.robot.step(self.timestep)
        self.robot.step(self.timestep)
        self.min_pelvis_height = self._pelvis_height()
        self.max_end_effector_speed = 0.0
        self.shelf_contacts = 0
        self.fall_detected = False
        self._previous_hand = None
        return self.observe()

    def step(self, joint_targets: dict[str, float]) -> dict:
        for name, motor in self.motors.items():
            motor.setPosition(float(joint_targets.get(name, 0.0)))
        self.robot.step(self.timestep)
        return self.observe()

    def safe_stop(self) -> dict:
        for name, motor in self.motors.items():
            motor.setPosition(float(self.sensors[name].getValue()))
        self.robot.step(self.timestep)
        return self.observe()

    # -- measurement -------------------------------------------------------
    def _pelvis_height(self) -> float:
        return float(self.self_node.getPosition()[2])

    def end_effector(self) -> np.ndarray:
        return np.array(self.hand_node.getPosition(), dtype=np.float64)

    def joint_angles(self) -> dict[str, float]:
        return {name: float(sensor.getValue()) for name, sensor in self.sensors.items()}

    def base_rotation(self) -> np.ndarray:
        return np.array(self.self_node.getOrientation(), dtype=np.float64).reshape(3, 3)

    def observe(self) -> dict:
        height = self._pelvis_height()
        self.min_pelvis_height = min(self.min_pelvis_height, height)
        if height < FALL_THRESHOLD_M:
            self.fall_detected = True

        hand = self.end_effector()
        speed = 0.0
        if self._previous_hand is not None:
            speed = float(np.linalg.norm(hand - self._previous_hand)) / (self.timestep / 1000.0)
        self._previous_hand = hand
        self.max_end_effector_speed = max(self.max_end_effector_speed, speed)

        contacts = sum(len(node.getContactPoints()) for node in self.shelf_nodes)
        self.shelf_contacts += contacts

        orientation = self.base_rotation()
        pitch = math.atan2(-orientation[2, 0], math.hypot(orientation[2, 1], orientation[2, 2]))
        roll = math.atan2(orientation[2, 1], orientation[2, 2])

        return {
            "sim_time": self.robot.getTime(),
            "pelvis_height": height,
            "end_effector": hand,
            "end_effector_speed": speed,
            "torso_roll": roll,
            "torso_pitch": pitch,
            "shelf_contacts_step": contacts,
            "upright": height >= FALL_THRESHOLD_M,
        }


def run(max_duration_seconds: float) -> dict:
    environment = WebotsInspectionEnvironment()
    controller = ShelfInspectionController(budget_seconds=max_duration_seconds)
    observation = environment.reset(controller.reset(environment.joint_limits()))

    control_steps = 0
    plan = None
    # Measured exactly as episode.py measures it, so the three engines report
    # the same quantity: the episode peak lands in RETURN, where the stance
    # pose is commanded as a step, so the inspecting speed is reported too.
    max_task_phase_speed = 0.0
    previous_hand = None
    while observation["sim_time"] < max_duration_seconds:
        angles = environment.joint_angles()
        jacobian = kinematics.jacobian(angles, base_rotation=environment.base_rotation())
        plan = controller.step(
            environment.end_effector(), jacobian, observation["sim_time"], angles
        )
        phase = controller.state.phase
        observation = environment.step(plan.joint_targets)
        hand = environment.end_effector()
        if previous_hand is not None and phase in ("REACH", "VERIFY"):
            travelled = float(np.linalg.norm(hand - previous_hand))
            max_task_phase_speed = max(
                max_task_phase_speed, travelled / (environment.timestep / 1000.0)
            )
        previous_hand = hand
        control_steps += 1
        if environment.fall_detected or controller.finished:
            break

    diagnostics = controller.diagnostics()
    completed = diagnostics["targets_completed"]
    errors = [entry["final_error_m"] for entry in diagnostics["per_target"] if entry["reached"]]
    success = (
        completed == REQUIRED_TARGETS
        and not environment.fall_detected
        and environment.shelf_contacts == 0
    )

    return {
        "simulator_engine": "Webots",
        "robot_model": "Boston Dynamics Atlas v4",
        "model_source": MODEL_SOURCE,
        "base": "free-standing (no weld, no external support)",
        "task": "inspect_shelf",
        "policy_id": POLICY_ID,
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": (
            "fall" if environment.fall_detected
            else "sequence_complete" if controller.finished
            else "time_limit"
        ),
        "safe_stop_applied": False,
        "sim_duration_seconds": round(float(observation["sim_time"]), 3),
        "control_steps": control_steps,
        "targets_total": REQUIRED_TARGETS,
        "targets_completed": completed,
        "mean_position_error_m": round(sum(errors) / len(errors), 5) if errors else None,
        "max_position_error_m": round(max(errors), 5) if errors else None,
        "final_pelvis_height_m": round(float(observation["pelvis_height"]), 4),
        "min_pelvis_height_m": round(float(environment.min_pelvis_height), 4),
        "fall_threshold_m": FALL_THRESHOLD_M,
        "fall_detected": environment.fall_detected,
        "shelf_contacts": environment.shelf_contacts,
        "max_end_effector_speed_mps": round(environment.max_end_effector_speed, 4),
        "max_end_effector_speed_inspecting_mps": round(max_task_phase_speed, 4),
        "final_torso_roll_rad": round(float(observation["torso_roll"]), 4),
        "final_torso_pitch_rad": round(float(observation["torso_pitch"]), 4),
        "final_phase": plan.phase if plan else "STAND",
        "policy_state": diagnostics,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-duration", type=float, default=EPISODE_BUDGET_S)
    args, _ = parser.parse_known_args()

    result = run(args.max_duration)
    destination = os.environ.get("ATLAS_WEBOTS_RESULT")
    if destination:
        Path(destination).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
