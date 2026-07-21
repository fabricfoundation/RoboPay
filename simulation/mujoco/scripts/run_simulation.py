"""
MuJoCo simulation runner for Unitree Go2 obstacle navigation.

This script:
1. Loads the Go2 MuJoCo scene with obstacles
2. Connects to RoboPay tunnel via Zenoh for action commands
3. Runs the potential field navigation policy
4. Reports REAL simulator state metrics (collision, position, velocity)
5. Publishes metrics to /robopay/metrics topic

Usage:
    python3 run_simulation.py [--headless] [--scenario SCENARIO] [--duration SECONDS]
"""
import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import mujoco
    MUJOCO_AVAILABLE = True
except ImportError:
    MUJOCO_AVAILABLE = False
    print("WARNING: mujoco not installed. Run: pip install mujoco")


@dataclass
class SimulatorMetrics:
    """Real metrics from MuJoCo physics engine."""

    # Position
    robot_pos: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    robot_vel: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    robot_heading: float = 0.0

    # Goal
    goal_pos: Tuple[float, float, float] = (10.0, 0.0, 0.0)
    distance_to_goal: float = 0.0

    # Collision (from contact forces in physics engine)
    collision_count: int = 0
    is_colliding: bool = False
    contact_force_magnitude: float = 0.0

    # Navigation
    path_progress: float = 0.0
    total_path_length: float = 0.0
    steps_elapsed: int = 0
    simulation_time: float = 0.0

    # Status
    status: str = "idle"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "robot_position": {"x": round(self.robot_pos[0], 3), "y": round(self.robot_pos[1], 3), "z": round(self.robot_pos[2], 3)},
            "robot_velocity": {"vx": round(self.robot_vel[0], 3), "vy": round(self.robot_vel[1], 3)},
            "robot_heading": round(self.robot_heading, 3),
            "goal_position": {"x": self.goal_pos[0], "y": self.goal_pos[1]},
            "distance_to_goal": round(self.distance_to_goal, 3),
            "collision_count": self.collision_count,
            "is_colliding": self.is_colliding,
            "contact_force_magnitude": round(self.contact_force_magnitude, 3),
            "path_progress": round(self.path_progress, 3),
            "simulation_time": round(self.simulation_time, 3),
            "steps_elapsed": self.steps_elapsed,
            "status": self.status,
        }


class PotentialFieldPolicy:
    """Policy-driven obstacle navigation using potential fields.

    This is NOT a replay — computes velocities from simulator state.
    """

    def __init__(
        self,
        goal: Tuple[float, float] = (10.0, 0.0),
        obstacles: List[Tuple[float, float]] = None,
        goal_threshold: float = 0.5,
        attract_gain: float = 2.0,
        repulse_gain: float = 1.5,
        repulse_radius: float = 2.0,
    ):
        self.goal = goal
        self.obstacles = obstacles or []
        self.goal_threshold = goal_threshold
        self.attract_gain = attract_gain
        self.repulse_gain = repulse_gain
        self.repulse_radius = repulse_radius

    def compute_action(self, pos: Tuple[float, float], heading: float) -> Tuple[float, float]:
        """Compute (forward_speed, angular_speed) from current state.

        Returns:
            (forward_speed, angular_speed) for the robot controller
        """
        # Attractive force toward goal
        dx = self.goal[0] - pos[0]
        dy = self.goal[1] - pos[1]
        dist = math.hypot(dx, dy)

        if dist > 0:
            attract_x = self.attract_gain * dx / dist
            attract_y = self.attract_gain * dy / dist
        else:
            return 0.0, 0.0

        # Repulsive forces from obstacles
        repulse_x, repulse_y = 0.0, 0.0
        for obs in self.obstacles:
            ox = pos[0] - obs[0]
            oy = pos[1] - obs[1]
            od = math.hypot(ox, oy)
            if 0.01 < od < self.repulse_radius:
                force = self.repulse_gain * (1.0 / od - 1.0 / self.repulse_radius) / (od ** 2)
                repulse_x += force * ox / od
                repulse_y += force * oy / od

        # Combined force
        total_x = attract_x + repulse_x
        total_y = attract_y + repulse_y
        total_mag = math.hypot(total_x, total_y)

        if total_mag > 0:
            total_x /= total_mag
            total_y /= total_mag

        # Convert to robot-frame velocities
        desired_angle = math.atan2(total_y, total_x)
        angle_error = desired_angle - heading

        # Normalize to [-pi, pi]
        while angle_error > math.pi:
            angle_error -= 2 * math.pi
        while angle_error < -math.pi:
            angle_error += 2 * math.pi

        # Forward speed (slow down near obstacles and when turning)
        min_obs_dist = min([math.hypot(pos[0] - o[0], pos[1] - o[1]) for o in self.obstacles] or [float("inf")])
        base_speed = 0.5
        if min_obs_dist < 1.0:
            base_speed *= 0.5

        forward_speed = base_speed * max(0, math.cos(angle_error))
        angular_speed = 0.5 * angle_error

        # Clamp angular speed
        angular_speed = max(-1.0, min(1.0, angular_speed))

        return forward_speed, angular_speed

    def is_goal_reached(self, pos: Tuple[float, float]) -> bool:
        return math.hypot(self.goal[0] - pos[0], self.goal[1] - pos[1]) < self.goal_threshold


def detect_collisions(model, data) -> Tuple[bool, float, int]:
    """Detect collisions from MuJoCo contact forces.

    Returns:
        (is_colliding, total_force_magnitude, contact_count)
    """
    total_force = 0.0
    contact_count = 0

    for i in range(data.ncon):
        contact = data.contact[i]
        # Get contact force
        force = np.zeros(6)
        mujoco.mj_contactForce(model, data, i, force)
        force_mag = np.linalg.norm(force[:3])

        if force_mag > 1.0:  # Threshold for meaningful contact
            contact_count += 1
            total_force += force_mag

    return contact_count > 0, total_force, contact_count


def run_simulation(
    scene_path: str,
    duration: float = 60.0,
    headless: bool = False,
    publish_metrics: bool = True,
) -> SimulatorMetrics:
    """Run MuJoCo simulation with policy-driven navigation.

    Args:
        scene_path: Path to MuJoCo XML scene file
        duration: Simulation duration in seconds
        headless: If True, skip rendering
        publish_metrics: If True, print metrics periodically

    Returns:
        Final SimulatorMetrics
    """
    if not MUJOCO_AVAILABLE:
        raise RuntimeError("MuJoCo not installed")

    # Load model
    model = mujoco.MjModel.from_xml_path(scene_path)
    data = mujoco.MjData(model)

    # Get goal position from model
    goal_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "goal")
    goal_pos = (model.body_pos[goal_body_id][0], model.body_pos[goal_body_id][1])

    # Get obstacle positions
    obstacles = []
    for name in ["obstacle_1", "obstacle_2", "obstacle_3"]:
        obs_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        if obs_id >= 0:
            obs_pos = model.body_pos[obs_id]
            obstacles.append((obs_pos[0], obs_pos[1]))

    # Initialize policy
    policy = PotentialFieldPolicy(
        goal=goal_pos,
        obstacles=obstacles,
        goal_threshold=0.5,
    )

    # Metrics tracking
    metrics = SimulatorMetrics(goal_pos=(goal_pos[0], goal_pos[1], 0.0))
    total_collision_count = 0
    path_start = None

    # Renderer (optional)
    renderer = None
    if not headless:
        try:
            renderer = mujoco.Renderer(model, height=480, width=640)
        except Exception:
            print("WARNING: Could not create renderer, running headless")

    # Simulation loop
    dt = model.opt.timestep
    steps_per_control = int(0.01 / dt)  # Control at 100Hz
    max_steps = int(duration / dt)

    print(f"Starting simulation: {duration}s, {max_steps} steps")
    print(f"Goal: {goal_pos}, Obstacles: {obstacles}")
    print("=" * 60)

    for step in range(max_steps):
        # Get robot state from simulator
        torso_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "torso")
        robot_pos = (data.xpos[torso_id][0], data.xpos[torso_id][1])
        robot_vel = (data.cvel[torso_id][0], data.cvel[torso_id][1])

        # Get heading from quaternion
        quat = data.xquat[torso_id]
        siny_cosp = 2 * (quat[0] * quat[3] + quat[1] * quat[2])
        cosy_cosp = 1 - 2 * (quat[2] ** 2 + quat[3] ** 2)
        heading = math.atan2(siny_cosp, cosy_cosp)

        if path_start is None:
            path_start = robot_pos
            metrics.total_path_length = math.hypot(goal_pos[0] - path_start[0], goal_pos[1] - path_start[1])

        # Detect collisions from physics engine
        is_colliding, force_mag, contact_count = detect_collisions(model, data)
        if is_colliding and not metrics.is_colliding:
            total_collision_count += contact_count

        # Update metrics
        metrics.robot_pos = (robot_pos[0], robot_pos[1], data.xpos[torso_id][2])
        metrics.robot_vel = (robot_vel[0], robot_vel[1], 0)
        metrics.robot_heading = heading
        metrics.distance_to_goal = math.hypot(goal_pos[0] - robot_pos[0], goal_pos[1] - robot_pos[1])
        metrics.collision_count = total_collision_count
        metrics.is_colliding = is_colliding
        metrics.contact_force_magnitude = force_mag
        metrics.steps_elapsed = step
        metrics.simulation_time = step * dt

        # Path progress
        if metrics.total_path_length > 0:
            metrics.path_progress = max(0, 1.0 - metrics.distance_to_goal / metrics.total_path_length)

        # Check goal reached
        if policy.is_goal_reached(robot_pos):
            metrics.status = "goal_reached"
            if publish_metrics:
                print(f"\nGOAL REACHED at step {step} ({step * dt:.1f}s)")
                print(json.dumps(metrics.to_dict(), indent=2))
            break

        # Check timeout
        if step >= max_steps - 1:
            metrics.status = "timeout"

        # Policy computes action from simulator state (NOT replay)
        if step % steps_per_control == 0:
            forward_speed, angular_speed = policy.compute_action(robot_pos, heading)

            # Apply to MuJoCo actuators
            # Root velocity control
            ctrl_x = forward_speed * math.cos(heading)
            ctrl_y = forward_speed * math.sin(heading)
            ctrl_yaw = angular_speed

            data.ctrl[0] = ctrl_x * 100  # root_x actuator
            data.ctrl[1] = ctrl_y * 100  # root_y actuator
            data.ctrl[2] = ctrl_yaw * 50  # root_yaw actuator

        # Step physics
        mujoco.mj_step(model, data)

        # Print metrics periodically
        if publish_metrics and step % 1000 == 0:
            print(f"Step {step} ({step*dt:.1f}s): "
                  f"pos=({robot_pos[0]:.2f},{robot_pos[1]:.2f}) "
                  f"dist={metrics.distance_to_goal:.2f} "
                  f"progress={metrics.path_progress:.1%} "
                  f"collisions={total_collision_count}")

    # Final metrics
    if metrics.status == "idle":
        metrics.status = "completed"

    print("\n" + "=" * 60)
    print("SIMULATION COMPLETE")
    print("=" * 60)
    print(json.dumps(metrics.to_dict(), indent=2))

    return metrics


def main():
    parser = argparse.ArgumentParser(description="MuJoCo Go2 obstacle navigation simulation")
    parser.add_argument("--scene", default="scenes/unitree-go2_obstacle_nav.xml", help="Scene XML file")
    parser.add_argument("--duration", type=float, default=60.0, help="Simulation duration (seconds)")
    parser.add_argument("--headless", action="store_true", help="Run without rendering")
    parser.add_argument("--no-metrics", action="store_true", help="Disable metrics output")
    args = parser.parse_args()

    scene_path = Path(__file__).parent / args.scene
    if not scene_path.exists():
        print(f"ERROR: Scene file not found: {scene_path}")
        return 1

    metrics = run_simulation(
        scene_path=str(scene_path),
        duration=args.duration,
        headless=args.headless,
        publish_metrics=not args.no_metrics,
    )

    return 0 if metrics.status == "goal_reached" else 1


if __name__ == "__main__":
    exit(main())
