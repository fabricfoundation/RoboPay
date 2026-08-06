"""MuJoCo obstacle-course environment using the pinned Spot MJCF model."""

from __future__ import annotations

import math
from xml.sax.saxutils import escape

import mujoco
import numpy as np

from .model import model_assets, resolve_model_dir
from .course import COURSE_GOAL, COURSE_OBSTACLES, COURSE_START_YAW_RAD


def _yaw_from_quaternion(quaternion: np.ndarray) -> float:
    w, x, y, z = quaternion
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _course_xml() -> str:
    obstacle_xml = "\n".join(
        f'''<body name="{item["name"]}" pos="{item["x"]} {item["y"]} {item["height"] / 2.0}">
              <geom name="{item["name"]}_geom" type="box" size="{item["half_x"]} {item["half_y"]} {item["height"] / 2.0}"
                    rgba="0.80 0.16 0.10 1" friction="1.2 0.02 0.01"/>
            </body>'''
        for item in COURSE_OBSTACLES
    )
    return f'''<mujoco model="robopay_spot_obstacle_course">
      <include file="spot.xml"/>
      <option timestep="0.002" integrator="implicitfast"/>
      <visual><global azimuth="210" elevation="-18"/></visual>
      <worldbody>
        <light name="course_light" pos="0 0 5" directional="true"/>
        <geom name="floor" type="plane" size="6 6 0.1" rgba="0.18 0.22 0.27 1" friction="1.2 0.02 0.01"/>
        {obstacle_xml}
        <body name="goal_marker" pos="{COURSE_GOAL[0]} {COURSE_GOAL[1]} 0.015">
          <geom type="cylinder" size="0.22 0.015" contype="0" conaffinity="0" rgba="0.10 0.80 0.25 0.55"/>
        </body>
      </worldbody>
    </mujoco>'''


class SpotObstacleCourseEnvironment:
    """A physics-backed course; no kinematic movement shortcuts are used."""

    def __init__(self, model_dir: str | None = None):
        self.model_dir = resolve_model_dir(model_dir)
        self.model = mujoco.MjModel.from_xml_string(_course_xml(), assets=model_assets(self.model_dir))
        self.data = mujoco.MjData(self.model)
        self.body_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "body")
        if self.body_id < 0:
            raise RuntimeError("Spot MJCF did not expose its root body named 'body'.")
        self._obstacle_geom_ids = {
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, f'{item["name"]}_geom')
            for item in COURSE_OBSTACLES
        }
        self.collision_count = 0
        self._previous_position: np.ndarray | None = None
        self.path_length = 0.0
        self.min_clearance = float("inf")

    def reset(self) -> dict:
        mujoco.mj_resetDataKeyframe(self.model, self.data, 0)
        self.data.qpos[3:7] = (
            math.cos(COURSE_START_YAW_RAD / 2.0),
            0.0,
            0.0,
            math.sin(COURSE_START_YAW_RAD / 2.0),
        )
        mujoco.mj_forward(self.model, self.data)
        self.collision_count = 0
        self._previous_position = self.data.xpos[self.body_id].copy()
        self.path_length = 0.0
        self.min_clearance = float("inf")
        return self.observe()

    def _contacts_with_obstacle(self) -> int:
        count = 0
        for contact_index in range(self.data.ncon):
            contact = self.data.contact[contact_index]
            if contact.geom1 in self._obstacle_geom_ids or contact.geom2 in self._obstacle_geom_ids:
                count += 1
        return count

    def _clearance(self, position: np.ndarray) -> float:
        # This is the root-body point's geometric distance to the obstacle
        # surface. Actual robot-vs-obstacle contact is recorded independently
        # from MuJoCo contacts, so this metric never pretends to be a collision
        # test or silently inflates the robot geometry.
        values = []
        for obstacle in COURSE_OBSTACLES:
            dx = max(abs(position[0] - obstacle["x"]) - obstacle["half_x"], 0.0)
            dy = max(abs(position[1] - obstacle["y"]) - obstacle["half_y"], 0.0)
            values.append(math.hypot(dx, dy))
        return min(values)

    def observe(self) -> dict:
        position = self.data.xpos[self.body_id].copy()
        quaternion = self.data.qpos[3:7].copy()
        clearance = self._clearance(position)
        return {
            "sim_time": float(self.data.time),
            "position": position,
            "yaw": _yaw_from_quaternion(quaternion),
            "body_height": float(position[2]),
            "goal": COURSE_GOAL,
            "clearance": clearance,
            "collision_count": self.collision_count,
        }

    def step(self, control: np.ndarray) -> dict:
        if control.shape != (self.model.nu,):
            raise ValueError(f"Expected {self.model.nu} actuator values, got {control.shape}.")
        self.data.ctrl[:] = control
        mujoco.mj_step(self.model, self.data)
        position = self.data.xpos[self.body_id].copy()
        if self._previous_position is not None:
            self.path_length += float(np.linalg.norm(position[:2] - self._previous_position[:2]))
        self._previous_position = position
        self.collision_count += self._contacts_with_obstacle()
        self.min_clearance = min(self.min_clearance, self._clearance(position))
        return self.observe()

    def safe_stop(self, neutral_control: np.ndarray) -> dict:
        """Apply a simulator emergency stop without advancing the episode."""

        if neutral_control.shape != (self.model.nu,):
            raise ValueError(
                f"Expected {self.model.nu} safe-stop actuator values, got {neutral_control.shape}."
            )
        self.data.ctrl[:] = neutral_control
        self.data.qvel[:] = 0.0
        mujoco.mj_forward(self.model, self.data)
        position = self.data.xpos[self.body_id].copy()
        self._previous_position = position
        self.min_clearance = min(self.min_clearance, self._clearance(position))
        return self.observe()
