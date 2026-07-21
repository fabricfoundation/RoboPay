"""
Webots controller for Unitree G1 obstacle navigation.

Runs the SAME policy as MuJoCo for Sim-to-Sim validation.
Reports metrics from Webots physics engine.

Usage:
    Place in simulation/webots/controllers/cra_navigation/
"""
import json
import math
import sys

try:
    from controller import Robot, GPS, InertialUnit, TouchSensor, Motor
    WEBOTS_CONTROLLER = True
except ImportError:
    WEBOTS_CONTROLLER = False
    print("WARNING: Not running in Webots controller context")


class PotentialFieldPolicy:
    """Same policy as MuJoCo — ensures consistent behavior across simulators."""

    def __init__(self, goal=(10.0, 0.0), obstacles=None, goal_threshold=0.5):
        self.goal = goal
        self.obstacles = obstacles or []
        self.goal_threshold = goal_threshold
        self.attract_gain = 2.0
        self.repulse_gain = 1.5
        self.repulse_radius = 2.0

    def compute_action(self, pos, heading):
        dx = self.goal[0] - pos[0]
        dy = self.goal[1] - pos[1]
        dist = math.hypot(dx, dy)

        if dist > 0:
            attract_x = self.attract_gain * dx / dist
            attract_y = self.attract_gain * dy / dist
        else:
            return 0.0, 0.0

        repulse_x, repulse_y = 0.0, 0.0
        for obs in self.obstacles:
            ox = pos[0] - obs[0]
            oy = pos[1] - obs[1]
            od = math.hypot(ox, oy)
            if 0.01 < od < self.repulse_radius:
                force = self.repulse_gain * (1.0 / od - 1.0 / self.repulse_radius) / (od ** 2)
                repulse_x += force * ox / od
                repulse_y += force * oy / od

        total_x = attract_x + repulse_x
        total_y = attract_y + repulse_y
        total_mag = math.hypot(total_x, total_y)

        if total_mag > 0:
            total_x /= total_mag
            total_y /= total_mag

        desired_angle = math.atan2(total_y, total_x)
        angle_error = desired_angle - heading
        while angle_error > math.pi:
            angle_error -= 2 * math.pi
        while angle_error < -math.pi:
            angle_error += 2 * math.pi

        min_obs_dist = min([math.hypot(pos[0] - o[0], pos[1] - o[1]) for o in self.obstacles] or [float("inf")])
        base_speed = 0.5
        if min_obs_dist < 1.0:
            base_speed *= 0.5

        forward_speed = base_speed * max(0, math.cos(angle_error))
        angular_speed = 0.15 * angle_error
        angular_speed = max(-0.3, min(0.3, angular_speed))

        return forward_speed, angular_speed

    def is_goal_reached(self, pos):
        return math.hypot(self.goal[0] - pos[0], self.goal[1] - pos[1]) < self.goal_threshold


def run_webots_simulation():
    """Run obstacle navigation in Webots."""
    if not WEBOTS_CONTROLLER:
        print("ERROR: Not in Webots controller context")
        return

    robot = Robot()
    timestep = int(robot.getBasicTimeStep())

    # Sensors
    gps = robot.getDevice("gps")
    gps.enable(timestep)

    imu = robot.getDevice("inertial_unit")
    imu.enable(timestep)

    # Collision sensors
    touch_left = robot.getDevice("left_foot_touch")
    touch_left.enable(timestep)
    touch_right = robot.getDevice("right_foot_touch")
    touch_right.enable(timestep)

    # Motors
    left_wheel = robot.getDevice("left_wheel")
    right_wheel = robot.getDevice("right_wheel")

    # Policy
    obstacles = [(2.5, 0.0), (5.0, 1.5), (7.0, -1.0)]
    policy = PotentialFieldPolicy(goal=(10.0, 0.0), obstacles=obstacles)

    # Metrics
    collision_count = 0
    path_start = None
    step = 0

    print("Webots simulation started")
    print(f"Goal: {policy.goal}, Obstacles: {obstacles}")
    print("=" * 60)

    while robot.step(timestep) != -1:
        # Get state from Webots sensors
        pos = gps.getValues()
        heading = imu.getRollPitchYaw()[2]

        if path_start is None:
            path_start = (pos[0], pos[1])

        # Collision detection from touch sensors
        if touch_left.getValue() > 0 or touch_right.getValue() > 0:
            collision_count += 1

        # Distance to goal
        dist_to_goal = math.hypot(policy.goal[0] - pos[0], policy.goal[1] - pos[1])
        total_dist = math.hypot(policy.goal[0] - path_start[0], policy.goal[1] - path_start[1])
        progress = max(0, 1.0 - dist_to_goal / total_dist) if total_dist > 0 else 1.0

        # Check goal
        if policy.is_goal_reached((pos[0], pos[1])):
            print(f"\nGOAL REACHED at step {step} ({step * timestep / 1000:.1f}s)")
            metrics = {
                "simulator": "webots",
                "robot_position": {"x": round(pos[0], 3), "y": round(pos[1], 3)},
                "robot_heading": round(heading, 3),
                "distance_to_goal": round(dist_to_goal, 3),
                "path_progress": round(progress, 3),
                "collision_count": collision_count,
                "simulation_time": round(step * timestep / 1000, 3),
                "steps_elapsed": step,
                "status": "goal_reached",
            }
            print(json.dumps(metrics, indent=2))
            break

        # Policy computes action
        forward_speed, angular_speed = policy.compute_action((pos[0], pos[1]), heading)

        # Apply to differential drive
        left_speed = forward_speed - angular_speed
        right_speed = forward_speed + angular_speed

        left_wheel.setVelocity(left_speed)
        right_wheel.setVelocity(right_speed)

        # Print metrics periodically
        if step % 500 == 0:
            print(f"Step {step} ({step*timestep/1000:.1f}s): "
                  f"pos=({pos[0]:.2f},{pos[1]:.2f}) "
                  f"dist={dist_to_goal:.2f} "
                  f"progress={progress:.1%} "
                  f"collisions={collision_count}")

        step += 1

    # Final metrics if timeout
    if not policy.is_goal_reached((pos[0], pos[1])):
        print("\nTIMEOUT")
        metrics = {
            "simulator": "webots",
            "robot_position": {"x": round(pos[0], 3), "y": round(pos[1], 3)},
            "distance_to_goal": round(dist_to_goal, 3),
            "path_progress": round(progress, 3),
            "collision_count": collision_count,
            "simulation_time": round(step * timestep / 1000, 3),
            "steps_elapsed": step,
            "status": "timeout",
        }
        print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    run_webots_simulation()
