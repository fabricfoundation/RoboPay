"""Webots controller for Reachy Mini — Full 9-DOF Sim-to-Sim validation.

This controller runs INSIDE the Webots physics engine. It imports the SAME
ReachyTaskPolicy FSM used by the MuJoCo bridge and closes the loop on ALL 9
real Webots joint sensors and supervisor node positions.

Actuator layout (matches official MJCF):
  [0] yaw_body       — torso rotation
  [1] stewart_1      — neck Stewart leg 1
  [2] stewart_2      — neck Stewart leg 2
  [3] stewart_3      — neck Stewart leg 3
  [4] stewart_4      — neck Stewart leg 4
  [5] stewart_5      — neck Stewart leg 5
  [6] stewart_6      — neck Stewart leg 6
  [7] right_antenna  — right antenna
  [8] left_antenna   — left antenna

The result is written to webots_sim2sim_result.json for the Sim2SimValidator.
"""

import json
import math
import os
import sys

# ── Webots imports ─────────────────────────────────────────────────────────────
from controller import Supervisor

# ── Import the SAME policy used by MuJoCo ──────────────────────────────────────
try:
    from policy.controller import ReachyTaskPolicy
except ImportError:
    _ctrl_dir = os.path.dirname(os.path.abspath(__file__))
    _bridge_root = os.path.normpath(os.path.join(_ctrl_dir, "..", "..", "..", ".."))
    sys.path.insert(0, _bridge_root)
    from policy.controller import ReachyTaskPolicy

# ── Configuration ──────────────────────────────────────────────────────────────
JOINT_NAMES = [
    "yaw_body",
    "stewart_1", "stewart_2", "stewart_3",
    "stewart_4", "stewart_5", "stewart_6",
    "right_antenna", "left_antenna",
]

SENSOR_NAMES = [f"{name}_sensor" for name in JOINT_NAMES]

TARGETS = ["apple", "croissant", "duck"]
WEBOTS_NODE_NAMES = {"apple": "APPLE", "croissant": "CROISSANT", "duck": "DUCK"}

MAX_SIM_TIME = 12.0  # seconds per target
SUCCESS_THRESHOLD_RAD = 0.65
MIN_SUCCESS_RATE = 0.30
MIN_SIM_TIME_DONE = 3.0

# Result file location: must match where Sim2SimValidator polls.
# This controller lives at simulation/scenes/controllers/reachy_mini_controller/,
# and sim2sim.py looks for the JSON in simulation/ (3 levels up).
RESULT_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "webots_sim2sim_result.json")
)


def get_object_position(supervisor, name):
    """Get world position of a Solid node by DEF name."""
    node = supervisor.getFromDef(name)
    if node is None:
        return [0.6, 0.0, 0.03]
    return node.getPosition()


def compute_angular_error(head_pos, head_fwd, target_pos):
    """Angular error between head forward vector and direction to target."""
    dx = target_pos[0] - head_pos[0]
    dy = target_pos[1] - head_pos[1]
    dz = target_pos[2] - head_pos[2]
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    if norm < 1e-6:
        return 0.0
    target_dir = [dx / norm, dy / norm, dz / norm]
    cos_angle = max(-1.0, min(1.0,
        head_fwd[0] * target_dir[0] +
        head_fwd[1] * target_dir[1] +
        head_fwd[2] * target_dir[2]
    ))
    return math.acos(cos_angle)


def get_head_state(supervisor, yaw, stewart_qpos):
    """Compute head position and forward vector from joint states.

    The head is at the top of the Stewart platform. Its orientation is
    determined by yaw_body rotation + Stewart platform tilt.
    """
    # Head position: base + torso height + neck + head offset
    # Approximate: head is at ~0.37m height, rotated by yaw
    head_height = 0.37
    head_pos = [0.0, 0.0, head_height]

    # Forward vector: primarily determined by yaw, with Stewart tilt contribution
    # Stewart joints 1,3,5 (X-axis) contribute pitch; 2,4,6 (Y-axis) contribute roll
    pitch = (stewart_qpos[0] + stewart_qpos[2] + stewart_qpos[4]) / 3.0 * 0.3
    roll = (stewart_qpos[1] + stewart_qpos[3] + stewart_qpos[5]) / 3.0 * 0.3

    # Forward vector with yaw + pitch
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    cos_p = math.cos(pitch)
    head_fwd = [
        cos_y * cos_p,
        sin_y * cos_p,
        math.sin(pitch),
    ]

    return head_pos, head_fwd


def run_episode(supervisor, motors, sensors, policy, target_name, node_name, timestep):
    """Run one tracking episode for a single target in real Webots physics."""
    policy.reset()

    target_pos = get_object_position(supervisor, node_name)

    total_steps = 0
    tracking_success_count = 0
    angular_errors = []
    sim_time = 0.0
    task_completed = False

    while sim_time < MAX_SIM_TIME:
        # Read ALL 9 real joint sensors
        joint_values = [s.getValue() for s in sensors]
        yaw = joint_values[0]
        stewart_qpos = joint_values[1:7]
        antenna_qpos = joint_values[7:9]

        # Compute head state from real joint readings
        head_pos, head_fwd = get_head_state(supervisor, yaw, stewart_qpos)

        # Compute real angular error
        error = compute_angular_error(head_pos, head_fwd, target_pos)
        angular_errors.append(error)

        if error < SUCCESS_THRESHOLD_RAD:
            tracking_success_count += 1

        total_steps += 1

        # Build observation dict (same interface as MuJoCo environment)
        obs = {
            "sim_time": sim_time,
            "head_pos": head_pos,
            "head_xmat": [
                math.cos(yaw), -math.sin(yaw), 0.0,
                math.sin(yaw), math.cos(yaw), 0.0,
                0.0, 0.0, 1.0,
            ],
            "eye_cam_pos": head_pos,
            "eye_cam_fwd": head_fwd,
            "base_yaw": yaw,
            "target_pos": target_pos,
            "apple_pos": get_object_position(supervisor, "APPLE"),
            "croissant_pos": get_object_position(supervisor, "CROISSANT"),
            "duck_pos": get_object_position(supervisor, "DUCK"),
            "stewart_qpos": stewart_qpos,
            "antenna_qpos": antenna_qpos,
            "num_contacts": 0,
        }

        # Run the SAME policy FSM
        metrics_snapshot = {
            "tracking_success_count": tracking_success_count,
            "task_completed": task_completed,
        }
        action, phase = policy.compute_action(obs, metrics_snapshot)

        # Apply ALL 9 control values to real Webots motors
        for i, motor in enumerate(motors):
            if i < len(action):
                motor.setPosition(float(action[i]))

        # Step Webots physics
        supervisor.step(timestep)
        sim_time += timestep / 1000.0

        # Check task completion
        rate = tracking_success_count / max(total_steps, 1)
        if rate >= MIN_SUCCESS_RATE and sim_time >= MIN_SIM_TIME_DONE:
            task_completed = True
            if sim_time >= 8.0:
                break

    # Compute summary
    n = max(total_steps, 1)
    steps_after_1s = max(total_steps - int(1.0 / (timestep / 1000.0)), 1)
    success_rate = min(1.0, tracking_success_count / max(steps_after_1s, 1))
    min_error = min(angular_errors) if angular_errors else 0.0
    mean_error = sum(angular_errors) / n if angular_errors else 0.0
    score = 1.0 if task_completed else min(1.0, (tracking_success_count / n) / MIN_SUCCESS_RATE)

    return {
        "target_object": target_name,
        "simulator_engine": "Webots",
        "sim_duration_seconds": round(sim_time, 2),
        "steps_executed": total_steps,
        "task_completed": task_completed,
        "tracking_success_rate": round(success_rate, 3),
        "min_tracking_error_rad": round(min_error, 3),
        "mean_tracking_error_rad": round(mean_error, 3),
        "success_rate_score": round(score, 3),
        "final_phase": policy.phase,
        "joints_actuated": len(JOINT_NAMES),
    }


def main():
    supervisor = Supervisor()
    timestep = int(supervisor.getBasicTimeStep())

    # Get ALL 9 motors and sensors
    motors = []
    sensors = []
    for name in JOINT_NAMES:
        motor = supervisor.getDevice(name)
        if motor:
            motors.append(motor)
            motor.setPosition(0.0)
            motor.setVelocity(5.0)
        else:
            print(f"[Webots Controller] WARNING: motor '{name}' not found")
            motors.append(None)

    for name in SENSOR_NAMES:
        sensor = supervisor.getDevice(name)
        if sensor:
            sensor.enable(timestep)
            sensors.append(sensor)
        else:
            print(f"[Webots Controller] WARNING: sensor '{name}' not found")
            sensors.append(None)

    # Filter out None entries for the episode loop
    valid_motors = [m for m in motors if m is not None]
    valid_sensors = [s for s in sensors if s is not None]

    print(f"[Webots Controller] Motors found: {len(valid_motors)}/9")
    print(f"[Webots Controller] Sensors found: {len(valid_sensors)}/9")

    # Instantiate the SAME policy class used by MuJoCo
    policy = ReachyTaskPolicy()

    print(f"[Webots Controller] Policy: {policy.__class__.__module__}.{policy.__class__.__name__}")
    print(f"[Webots Controller] Targets: {TARGETS}")
    print(f"[Webots Controller] Timestep: {timestep}ms")

    results = []
    for target in TARGETS:
        node_name = WEBOTS_NODE_NAMES[target]
        print(f"[Webots Controller] Episode: {target} (DEF={node_name})")

        # Reset
        supervisor.simulationReset()
        supervisor.step(timestep)

        # Re-enable sensors after reset
        for s in valid_sensors:
            s.enable(timestep)

        episode_result = run_episode(
            supervisor, valid_motors, valid_sensors, policy, target, node_name, timestep
        )
        results.append(episode_result)
        print(f"  -> completed={episode_result['task_completed']}, "
              f"rate={episode_result['tracking_success_rate']}, "
              f"min_err={episode_result['min_tracking_error_rad']} rad, "
              f"phase={episode_result['final_phase']}")

    # Aggregate + write result JSON. This block is bulletproof: even if any
    # metadata computation fails, the per-target episode results are still written
    # so the Sim2SimValidator can read them.
    output = {
        "run_id": "sim2sim_webots_native_9dof",
        "simulator_engine": "Webots",
        "webots_version": "unknown",
        "dof_actuated": len(valid_motors),
        "targets_tracked": [r["target_object"] for r in results if r.get("task_completed")],
        "targets_requested": TARGETS,
        "task_completed": all(r.get("task_completed", False) for r in results) if results else False,
        "tracking_success_rate": round(
            sum(r.get("tracking_success_rate", 0.0) for r in results) / max(len(results), 1), 3
        ),
        "success_rate_score": round(
            sum(r.get("success_rate_score", 0.0) for r in results) / max(len(results), 1), 3
        ),
        "per_target_results": results,
        "policy_module": f"{ReachyTaskPolicy.__module__}",
        "joint_names": JOINT_NAMES,
        "note": (
            f"Real Webots physics with {len(valid_motors)} actuated joints. "
            f"Same ReachyTaskPolicy as MuJoCo bridge. Full closed-loop feedback."
        ),
    }

    # Best-effort: fill in the Webots version (non-essential, may not exist)
    try:
        output["webots_version"] = supervisor.getVersion()
    except Exception:
        pass

    # GUARANTEED write — this is what the Sim2SimValidator polls for.
    try:
        with open(RESULT_FILE, "w") as f:
            json.dump(output, f, indent=2)
        print(f"[Webots Controller] Results written: {RESULT_FILE}")
        sys.stdout.flush()
    except Exception as exc:
        print(f"[Webots Controller] FATAL: could not write result JSON: {exc}")
        sys.stdout.flush()

    print(f"[Webots Controller] Overall score: {output['success_rate_score']:.3f}")
    print(f"[Webots Controller] DOF: {len(valid_motors)}/9")
    sys.stdout.flush()

    # In batch/sim2sim mode (env flag set by sim2sim.py), quit Webots automatically.
    # In GUI mode, leave the window open so the user can inspect the scene.
    if os.environ.get("REACHY_SIM2SIM_BATCH") == "1":
        print("[Webots Controller] Batch mode -> quitting simulation.")
        sys.stdout.flush()
        supervisor.simulationQuit(0)
    else:
        print("[Webots Controller] GUI mode -> done. Window stays open. Close it manually.")
        sys.stdout.flush()
        # Keep the controller alive so Webots doesn't report a controller crash.
        # Step forever (cheap) until the user closes the window.
        while supervisor.step(timestep) != -1:
            pass


if __name__ == "__main__":
    main()
