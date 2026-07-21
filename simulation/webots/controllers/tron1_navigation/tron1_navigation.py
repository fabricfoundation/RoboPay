"""
Webots controller for LimX TRON1 obstacle navigation.
Runs the SAME policy as MuJoCo for Sim-to-Sim validation.
Velocity limits: vx [0, 0.8], wz [-0.3, 0.3]
"""
import json
import math

try:
    from controller import Robot, GPS, InertialUnit, TouchSensor, Motor
    WEBOTS_CONTROLLER = True
except ImportError:
    WEBOTS_CONTROLLER = False

class PotentialFieldPolicy:
    VX_MIN, VX_MAX = 0.0, 0.8
    WZ_MIN, WZ_MAX = -0.3, 0.3

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
            ox, oy = pos[0] - obs[0], pos[1] - obs[1]
            od = math.hypot(ox, oy)
            if 0.01 < od < self.repulse_radius:
                force = self.repulse_gain * (1.0 / od - 1.0 / self.repulse_radius) / (od ** 2)
                repulse_x += force * ox / od
                repulse_y += force * oy / od
        total_x, total_y = attract_x + repulse_x, attract_y + repulse_y
        total_mag = math.hypot(total_x, total_y)
        if total_mag > 0:
            total_x /= total_mag; total_y /= total_mag
        desired_angle = math.atan2(total_y, total_x)
        angle_error = desired_angle - heading
        while angle_error > math.pi: angle_error -= 2 * math.pi
        while angle_error < -math.pi: angle_error += 2 * math.pi
        min_obs_dist = min([math.hypot(pos[0] - o[0], pos[1] - o[1]) for o in self.obstacles] or [float("inf")])
        base_speed = 0.25 if min_obs_dist < 1.0 else 0.5
        forward_speed = max(self.VX_MIN, min(self.VX_MAX, base_speed * max(0, math.cos(angle_error))))
        angular_speed = max(self.WZ_MIN, min(self.WZ_MAX, 0.5 * angle_error))
        return forward_speed, angular_speed

    def is_goal_reached(self, pos):
        return math.hypot(self.goal[0] - pos[0], self.goal[1] - pos[1]) < self.goal_threshold

def run_webots_simulation():
    if not WEBOTS_CONTROLLER:
        return
    robot = Robot()
    timestep = int(robot.getBasicTimeStep())
    gps = robot.getDevice("gps"); gps.enable(timestep)
    imu = robot.getDevice("inertial_unit"); imu.enable(timestep)
    touch_left = robot.getDevice("left_foot_touch"); touch_left.enable(timestep)
    touch_right = robot.getDevice("right_foot_touch"); touch_right.enable(timestep)
    left_wheel = robot.getDevice("left_wheel")
    right_wheel = robot.getDevice("right_wheel")
    obstacles = [(2.5, 0.0), (5.0, 1.5), (7.0, -1.0)]
    policy = PotentialFieldPolicy(goal=(10.0, 0.0), obstacles=obstacles)
    collision_count = 0; path_start = None; step = 0
    print("TRON1 Webots simulation started")
    while robot.step(timestep) != -1:
        pos = gps.getValues(); heading = imu.getRollPitchYaw()[2]
        if path_start is None: path_start = (pos[0], pos[1])
        if touch_left.getValue() > 0 or touch_right.getValue() > 0: collision_count += 1
        dist_to_goal = math.hypot(policy.goal[0] - pos[0], policy.goal[1] - pos[1])
        total_dist = math.hypot(policy.goal[0] - path_start[0], policy.goal[1] - path_start[1])
        progress = max(0, 1.0 - dist_to_goal / total_dist) if total_dist > 0 else 1.0
        if policy.is_goal_reached((pos[0], pos[1])):
            print(json.dumps({"simulator":"webots","robot":"tron1","status":"goal_reached","path_progress":round(progress,3),"collision_count":collision_count}))
            break
        forward_speed, angular_speed = policy.compute_action((pos[0], pos[1]), heading)
        left_wheel.setVelocity(forward_speed - angular_speed)
        right_wheel.setVelocity(forward_speed + angular_speed)
        step += 1
    if not policy.is_goal_reached((pos[0], pos[1])):
        print(json.dumps({"simulator":"webots","robot":"tron1","status":"timeout","path_progress":round(progress,3),"collision_count":collision_count}))

if __name__ == "__main__":
    run_webots_simulation()
