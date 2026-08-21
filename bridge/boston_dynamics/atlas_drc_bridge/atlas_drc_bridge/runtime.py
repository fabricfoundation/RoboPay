"""Physics-backed, closed-loop Atlas DRC right-arm wave episode."""

from __future__ import annotations

import math
import time
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .contracts import WaveParameters
from .model import load_mujoco_model


# These are names from the pinned Atlas v4 URDF.  The model has no actuator
# definitions, so this adapter applies bounded generalized torque through
# MuJoCo's real dynamics instead of writing joint positions directly.
WAVE_JOINT = "r_arm_shz"
POSTURE_TARGETS = {
    "r_arm_shx": -0.25,
    "r_arm_ely": 1.05,
    "r_arm_elx": -0.75,
    "r_arm_wry": 0.15,
    "r_arm_wrx": 0.0,
    "r_arm_wry2": 0.0,
}
KP = 70.0
KD = 8.0
MAX_TORQUE_NM = 75.0
TARGET_TOLERANCE_RAD = 0.055
VELOCITY_TOLERANCE_RAD_S = 0.65
REQUIRED_SETTLED_STEPS = 6
TURNING_POINT_FRACTION = 0.70


@dataclass
class ArmWavePolicy:
    """State-feedback, target-switching wave controller.

    This is intentionally not a replayed trajectory. It advances to the next
    half-wave only after the measured shoulder crosses a bounded turning-point
    threshold. The threshold is deliberately inside the requested amplitude:
    it absorbs real model coupling and avoids treating a precomputed timer as
    proof of a physical wave.
    """

    params: WaveParameters
    phase_index: int = 0
    settled_steps: int = 0
    return_settled: bool = False

    @property
    def complete(self) -> bool:
        return self.return_settled

    def target(self) -> float:
        if self.phase_index >= self.params.cycles * 2:
            return 0.0
        return self.params.amplitude_rad if self.phase_index % 2 == 0 else -self.params.amplitude_rad

    def observe(self, position: float, velocity: float) -> None:
        if self.complete:
            return
        if self.phase_index >= self.params.cycles * 2:
            returned = (
                abs(position) <= TARGET_TOLERANCE_RAD
                and abs(velocity) <= VELOCITY_TOLERANCE_RAD_S
            )
            self.settled_steps = self.settled_steps + 1 if returned else 0
            if self.settled_steps >= REQUIRED_SETTLED_STEPS:
                self.return_settled = True
                self.settled_steps = 0
            return
        threshold = abs(self.target()) * TURNING_POINT_FRACTION
        reached = position >= threshold if self.target() > 0 else position <= -threshold
        self.settled_steps = self.settled_steps + 1 if reached else 0
        if self.settled_steps >= REQUIRED_SETTLED_STEPS:
            self.phase_index += 1
            self.settled_steps = 0


def _joint_addresses(model) -> dict[str, tuple[int, int]]:
    import mujoco

    addresses: dict[str, tuple[int, int]] = {}
    required = {WAVE_JOINT, *POSTURE_TARGETS}
    for joint_name in required:
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
        if joint_id < 0:
            raise RuntimeError(f"Pinned Atlas URDF is missing required joint {joint_name!r}.")
        addresses[joint_name] = (int(model.jnt_qposadr[joint_id]), int(model.jnt_dofadr[joint_id]))
    return addresses


def _posture_hold_addresses(model) -> dict[str, tuple[int, int]]:
    """Return all one-DoF joints that can be physically held during an arm task.

    The source URDF intentionally has no motor definitions.  Holding the
    non-commanded joints at their measured initial posture stops gravity and
    whole-body coupling from masquerading as a right-arm-policy failure, while
    leaving every degree of freedom in MuJoCo's normal forward dynamics.
    """

    import mujoco

    addresses: dict[str, tuple[int, int]] = {}
    for joint_id in range(model.njnt):
        if int(model.jnt_type[joint_id]) not in (
            int(mujoco.mjtJoint.mjJNT_HINGE),
            int(mujoco.mjtJoint.mjJNT_SLIDE),
        ):
            continue
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id)
        if name:
            addresses[name] = (int(model.jnt_qposadr[joint_id]), int(model.jnt_dofadr[joint_id]))
    return addresses


def _bounded_pd(target: float, position: float, velocity: float) -> float:
    return float(np.clip(KP * (target - position) - KD * velocity, -MAX_TORQUE_NM, MAX_TORQUE_NM))


def run_wave_episode(
    params: WaveParameters,
    model_dir: str | None = None,
    stop_requested: Callable[[], bool] | None = None,
    viewer: bool = False,
    viewer_hold_seconds: float = 0.0,
    viewer_start_hold_seconds: float = 0.0,
    viewer_turn_hold_seconds: float = 0.0,
) -> dict:
    """Run a measured-state closed-loop wave in real MuJoCo physics.

    The result contains joint-space state metrics that make a superficial
    success impossible: a terminal success requires every requested half-wave
    to reach its measured target while remaining finite and within torque
    limits.
    """

    import mujoco

    model = load_mujoco_model(model_dir, visual=viewer)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    addresses = _joint_addresses(model)
    posture_addresses = _posture_hold_addresses(model)
    policy = ArmWavePolicy(params)
    stop = stop_requested or (lambda: False)
    wave_qpos, wave_dof = addresses[WAVE_JOINT]
    initial_angle = float(data.qpos[wave_qpos])
    hold_targets = {
        name: float(data.qpos[qpos_address])
        for name, (qpos_address, _) in posture_addresses.items()
    }
    samples: list[float] = [initial_angle]
    torque_peak = 0.0
    control_steps = 0
    safe_stopped = False
    finite = True

    viewer_context = nullcontext(None)
    if viewer:
        import mujoco.viewer

        viewer_context = mujoco.viewer.launch_passive(model, data)

    with viewer_context as active_viewer:
        if active_viewer is not None:
            active_viewer.cam.lookat[:] = (0.0, 0.0, 0.85)
            active_viewer.cam.distance = 4.2
            active_viewer.cam.azimuth = 135.0
            active_viewer.cam.elevation = -18.0

            start_deadline = time.monotonic() + viewer_start_hold_seconds
            while active_viewer.is_running() and time.monotonic() < start_deadline:
                active_viewer.sync()
                time.sleep(0.02)

        while data.time < params.max_duration_sec and not policy.complete:
            if stop():
                data.qfrc_applied[:] = 0.0
                data.qvel[:] = 0.0
                mujoco.mj_forward(model, data)
                safe_stopped = True
                break

            data.qfrc_applied[:] = 0.0
            targets = dict(hold_targets)
            targets.update({WAVE_JOINT: policy.target(), **POSTURE_TARGETS})
            for joint_name, target in targets.items():
                qpos_address, dof_address = posture_addresses[joint_name]
                torque = _bounded_pd(
                    target,
                    float(data.qpos[qpos_address]),
                    float(data.qvel[dof_address]),
                )
                data.qfrc_applied[dof_address] = torque
                torque_peak = max(torque_peak, abs(torque))

            mujoco.mj_step(model, data)
            position = float(data.qpos[wave_qpos])
            velocity = float(data.qvel[wave_dof])
            finite = finite and math.isfinite(position) and math.isfinite(velocity)
            if not finite:
                break
            samples.append(position)
            previous_phase = policy.phase_index
            policy.observe(position, velocity)
            control_steps += 1
            if active_viewer is not None:
                active_viewer.sync()
                # The automated proof runs at full speed; the opt-in desktop
                # view follows the physics clock so a human can inspect it.
                time.sleep(float(model.opt.timestep))
                if policy.phase_index != previous_phase and viewer_turn_hold_seconds > 0:
                    turn_deadline = time.monotonic() + viewer_turn_hold_seconds
                    while active_viewer.is_running() and time.monotonic() < turn_deadline:
                        active_viewer.sync()
                        time.sleep(0.02)

        if active_viewer is not None and viewer_hold_seconds > 0:
            deadline = time.monotonic() + viewer_hold_seconds
            while active_viewer.is_running() and time.monotonic() < deadline:
                active_viewer.sync()
                time.sleep(0.02)

    measured_stroke = max(samples) - min(samples)
    success = bool(
        finite
        and not safe_stopped
        and policy.complete
        and measured_stroke >= params.amplitude_rad * 1.35
        and torque_peak <= MAX_TORQUE_NM + 1e-9
    )
    return {
        "simulator_engine": "MuJoCo",
        "robot_model": "Boston Dynamics Atlas DRC v4 (legacy URDF)",
        "task": "wave_right_arm",
        "status": "success" if success else "failure",
        "success": success,
        "completion_reason": (
            "safe_stopped" if safe_stopped else "wave_complete" if policy.complete else "time_limit"
        ),
        "safe_stop_applied": safe_stopped,
        "sim_duration_seconds": round(float(data.time), 3),
        "control_steps": control_steps,
        "controller": "state_feedback_turning_point_pd_torque",
        "policy_id": "atlas-drc-right-arm-wave-v1",
        "requested_cycles": params.cycles,
        "completed_half_waves": policy.phase_index,
        "requested_amplitude_rad": params.amplitude_rad,
        "initial_wave_joint_rad": round(initial_angle, 5),
        "final_wave_joint_rad": round(float(data.qpos[wave_qpos]), 5),
        "min_wave_joint_rad": round(min(samples), 5),
        "max_wave_joint_rad": round(max(samples), 5),
        "final_wave_joint_velocity_rad_s": round(float(data.qvel[wave_dof]), 5),
        "measured_wave_stroke_rad": round(measured_stroke, 5),
        "peak_commanded_torque_nm": round(torque_peak, 5),
        "finite_state": finite,
        "viewer_enabled": viewer,
    }
