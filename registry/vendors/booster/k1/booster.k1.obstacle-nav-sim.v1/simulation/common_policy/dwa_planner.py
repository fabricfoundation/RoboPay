"""
Dynamic Window Approach (DWA) local planner for Booster K1 base navigation.

This module is simulator-agnostic: it only consumes a plain robot state
(x, y, yaw, v, omega) and a list of obstacle circles, and returns a
(v, omega) velocity command. Both the MuJoCo runner and the Webots
controller import this exact module, so sim-to-sim validation compares
two different physics engines driving the *same* policy code, not two
different policies.
"""
from dataclasses import dataclass, field
import numpy as np


@dataclass
class DWAConfig:
    max_speed: float = 0.6          # m/s
    min_speed: float = 0.0
    max_yaw_rate: float = 1.2       # rad/s
    max_accel: float = 2.0          # m/s^2
    max_yaw_accel: float = 4.0      # rad/s^2
    v_resolution: float = 0.05
    yaw_rate_resolution: float = 0.1
    dt: float = 0.1
    predict_time: float = 2.0
    robot_radius: float = 0.35
    goal_weight: float = 1.0
    obstacle_weight: float = 1.2
    speed_weight: float = 0.3


@dataclass
class RobotState:
    x: float
    y: float
    yaw: float
    v: float = 0.0
    omega: float = 0.0


def motion_predict(state: RobotState, v: float, omega: float, dt: float) -> RobotState:
    yaw = state.yaw + omega * dt
    x = state.x + v * np.cos(yaw) * dt
    y = state.y + v * np.sin(yaw) * dt
    return RobotState(x, y, yaw, v, omega)


def simulate_trajectory(state: RobotState, v: float, omega: float, cfg: DWAConfig):
    traj = [state]
    t = 0.0
    s = state
    while t <= cfg.predict_time:
        s = motion_predict(s, v, omega, cfg.dt)
        traj.append(s)
        t += cfg.dt
    return traj


def dynamic_window(state: RobotState, cfg: DWAConfig):
    vs = [cfg.min_speed, cfg.max_speed, -cfg.max_yaw_rate, cfg.max_yaw_rate]
    vd = [
        state.v - cfg.max_accel * cfg.dt, state.v + cfg.max_accel * cfg.dt,
        state.omega - cfg.max_yaw_accel * cfg.dt, state.omega + cfg.max_yaw_accel * cfg.dt,
    ]
    return [
        max(vs[0], vd[0]), min(vs[1], vd[1]),
        max(vs[2], vd[2]), min(vs[3], vd[3]),
    ]


def goal_cost(traj, goal):
    dx = goal[0] - traj[-1].x
    dy = goal[1] - traj[-1].y
    return float(np.hypot(dx, dy))


def obstacle_cost(traj, obstacles, robot_radius):
    if not obstacles:
        return 0.0
    min_dist = float("inf")
    for s in traj:
        for (ox, oy, orad) in obstacles:
            d = np.hypot(s.x - ox, s.y - oy) - orad - robot_radius
            min_dist = min(min_dist, d)
    if min_dist < 0:
        return float("inf")  # collision course, reject
    return 1.0 / max(min_dist, 1e-3)


def plan_step(state: RobotState, goal, obstacles, cfg: DWAConfig):
    """Returns (v, omega, best_traj). Pure function, no side effects."""
    dw = dynamic_window(state, cfg)
    best_cost = float("inf")
    best_u = (0.0, 0.0)
    best_traj = [state]

    v_range = np.arange(dw[0], dw[1] + cfg.v_resolution, cfg.v_resolution)
    w_range = np.arange(dw[2], dw[3] + cfg.yaw_rate_resolution, cfg.yaw_rate_resolution)

    for v in v_range:
        for w in w_range:
            traj = simulate_trajectory(state, v, w, cfg)
            gc = goal_cost(traj, goal)
            oc = obstacle_cost(traj, obstacles, cfg.robot_radius)
            if oc == float("inf"):
                continue
            sc = cfg.max_speed - v
            cost = cfg.goal_weight * gc + cfg.obstacle_weight * oc + cfg.speed_weight * sc
            if cost < best_cost:
                best_cost = cost
                best_u = (float(v), float(w))
                best_traj = traj

    return best_u[0], best_u[1], best_traj
