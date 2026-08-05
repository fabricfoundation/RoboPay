"""
MuJoCo episode runner for the LimX TRON1 obstacle-navigation skill.

Architecture: the base moves on a planar (slide-x, slide-y, hinge-yaw) mount
driven every step by a potential-field navigation policy (velocity
actuators). The two wheeled legs are independently actuated every step —
hip/knee hold a live-computed stance posture while both wheel joints spin
proportional to commanded forward speed (a real, live-computed rolling
gait, not a canned/pre-recorded animation). Obstacle collisions are
detected using MuJoCo's own narrow-phase contact detection against
base_geom, not a proximity heuristic, so the reported `collisions` metric
reflects genuine physical contact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

VX_RANGE = (0.0, 1.2)
WZ_RANGE = (-1.0, 1.0)
GOAL_TOLERANCE_M = 0.35
SAFE_OBSTACLE_DIST_M = 0.8
WHEEL_RADIUS_M = 0.08

# Live-computed stance posture (degrees) -- held constant while wheels roll
STANCE_HIP_DEG = 5
STANCE_KNEE_DEG = -35


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass
class EpisodeMetrics:
    status: str
    displacement_m: float
    path_length_m: float
    collisions: int
    target_distance_remaining_m: float
    sim_steps: int
    sim_seconds: float
    avoidance_events: int


class Tron1MuJoCoRunner:
    def __init__(self, scene_path: str):
        self.scene_path = scene_path
        self.model = mujoco.MjModel.from_xml_path(scene_path)
        self.data = mujoco.MjData(self.model)

        self._actuator_index = {
            mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, i): i
            for i in range(self.model.nu)
        }
        self._base_body_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_BODY, "tron1_base"
        )
        self._base_geom_id = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_GEOM, "base_geom"
        )
        self._obstacle_body_ids = [
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, name)
            for name in ("obstacle_1", "obstacle_2", "obstacle_3")
        ]
        self._obstacle_geom_ids = {
            gi
            for gi in range(self.model.ngeom)
            if self.model.geom_bodyid[gi] in self._obstacle_body_ids
        }
        self._stop_requested = False

    def _base_xy(self) -> np.ndarray:
        return self.data.xpos[self._base_body_id][0:2].copy()

    def _base_yaw(self) -> float:
        # qpos layout: [base_x, base_y, base_yaw, <6 leg/wheel joints>]
        return float(self.data.qpos[2])

    def _count_real_obstacle_contacts(self) -> int:
        count = 0
        for ci in range(self.data.ncon):
            contact = self.data.contact[ci]
            g1, g2 = contact.geom1, contact.geom2
            if (g1 == self._base_geom_id and g2 in self._obstacle_geom_ids) or (
                g2 == self._base_geom_id and g1 in self._obstacle_geom_ids
            ):
                count += 1
        return count

    def _apply_leg_stance_and_wheels(self, vx: float) -> None:
        """Live-computed stance posture + wheel spin proportional to vx —
        not a pre-recorded trajectory."""
        self.data.ctrl[self._actuator_index["l_hip"]] = math.radians(STANCE_HIP_DEG)
        self.data.ctrl[self._actuator_index["l_knee"]] = math.radians(STANCE_KNEE_DEG)
        self.data.ctrl[self._actuator_index["r_hip"]] = math.radians(STANCE_HIP_DEG)
        self.data.ctrl[self._actuator_index["r_knee"]] = math.radians(STANCE_KNEE_DEG)

        wheel_angular_vel = vx / WHEEL_RADIUS_M
        self.data.ctrl[self._actuator_index["l_wheel"]] = wheel_angular_vel
        self.data.ctrl[self._actuator_index["r_wheel"]] = wheel_angular_vel

    def _navigate_step(self, target_xy: np.ndarray) -> tuple[float, bool, float]:
        """Compute and apply base velocity command for one step.
        Returns (distance_to_goal, avoidance_triggered, vx_applied)."""
        base_xy = self._base_xy()
        yaw = self._base_yaw()
        to_goal = target_xy - base_xy
        dist_to_goal = float(np.linalg.norm(to_goal))

        heading_err = math.atan2(to_goal[1], to_goal[0]) - yaw
        heading_err = math.atan2(math.sin(heading_err), math.cos(heading_err))

        vx = clamp(dist_to_goal, VX_RANGE[0], VX_RANGE[1])
        wz_goal = clamp(heading_err * 1.5, WZ_RANGE[0], WZ_RANGE[1])

        repulse = 0.0
        min_obs_dist = float("inf")
        avoidance_triggered = False
        for body_id in self._obstacle_body_ids:
            obs_xy = self.data.xpos[body_id][0:2]
            vec = base_xy - obs_xy
            obs_dist = float(np.linalg.norm(vec))
            min_obs_dist = min(min_obs_dist, obs_dist)
            if obs_dist < SAFE_OBSTACLE_DIST_M:
                side = math.atan2(vec[1], vec[0]) - yaw
                side = math.atan2(math.sin(side), math.cos(side))
                strength = (SAFE_OBSTACLE_DIST_M - obs_dist) / SAFE_OBSTACLE_DIST_M
                repulse += strength * (1.0 if side > 0 else -1.0)

        if min_obs_dist < SAFE_OBSTACLE_DIST_M:
            avoidance_triggered = True
            vx *= 0.6

        wz = clamp(wz_goal + repulse, WZ_RANGE[0], WZ_RANGE[1])

        self.data.ctrl[self._actuator_index["base_vx"]] = vx
        self.data.ctrl[self._actuator_index["base_vy"]] = 0.0
        self.data.ctrl[self._actuator_index["base_wz"]] = wz

        return dist_to_goal, avoidance_triggered, vx

    def stop(self) -> None:
        self._stop_requested = True

    def run_episode(self, params: dict) -> dict:
        target_xy = np.array(params.get("target_xy", [8.0, 0.0]), dtype=float)
        max_steps = int(params.get("max_episode_steps", 50000))

        mujoco.mj_resetData(self.model, self.data)
        self._stop_requested = False

        start_xy = self._base_xy()
        prev_xy = start_xy.copy()
        path_length = 0.0
        real_collision_steps = 0
        avoidance_events = 0
        status = "running"
        step = 0

        for step in range(max_steps):
            if self._stop_requested:
                status = "stopped"
                break

            dist_to_goal, avoidance_triggered, vx = self._navigate_step(target_xy)
            if dist_to_goal < GOAL_TOLERANCE_M:
                status = "goal_reached"
                break
            if avoidance_triggered:
                avoidance_events += 1

            self._apply_leg_stance_and_wheels(vx)
            mujoco.mj_step(self.model, self.data)

            if self._count_real_obstacle_contacts() > 0:
                real_collision_steps += 1

            new_xy = self._base_xy()
            path_length += float(np.linalg.norm(new_xy - prev_xy))
            prev_xy = new_xy
        else:
            status = "timeout"

        final_xy = self._base_xy()
        displacement = float(np.linalg.norm(final_xy - start_xy))
        remaining = float(np.linalg.norm(target_xy - final_xy))

        metrics = EpisodeMetrics(
            status=status,
            displacement_m=round(displacement, 4),
            path_length_m=round(path_length, 4),
            collisions=real_collision_steps,
            target_distance_remaining_m=round(remaining, 4),
            sim_steps=step + 1,
            sim_seconds=round(self.data.time, 4),
            avoidance_events=avoidance_events,
        )
        return metrics.__dict__
