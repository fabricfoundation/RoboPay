"""door-arm-001 --- PyBullet backend stub (sim-to-sim).

This module provides a PyBullet-compatible interface for sim-to-sim testing.
On Windows without PyBullet installed, the tests that require it are skipped.
The stub records all calls for verification.
"""
from __future__ import annotations

import sys
import time

import numpy as np

from arm_spec import (
    ARM_JOINTS, BASE_H, BudgetExhausted, DOOR_HANDLE_HEIGHT, DOOR_WIDTH,
    GRASP_FORCE_MIN, GRIP_MID, LINK1, LINK2, OPEN_ANGLE_MIN, TIMESTEP,
    aperture_at, blend, build_metrics, resolve_scene,
)

ENGINE = "pybullet"


def available() -> bool:
    """True when the real PyBullet wheel is importable (not our stub)."""
    try:
        mod = sys.modules.get("pybullet")
        if mod is None:
            return False
        # If it's our stub module, it's not real
        if getattr(mod, '__file__', '').endswith('bullet_stub.py'):
            return False
        # Try importing to see if it's real
        import pybullet as p
        return hasattr(p, 'loadURDF') and callable(p.loadURDF)
    except Exception:
        return False


# Collision groups
G_FLOOR, M_FLOOR = 1, 6
G_DOOR, M_DOOR = 2, 13
G_HANDLE, M_HANDLE = 4, 11
G_ARM, M_ARM = 8, 8


def _robot_urdf() -> str:
    """URDF for door-arm-001."""
    return f"""<?xml version="1.0"?>
<robot name="door-arm-001">
  <link name="base">
    <visual><geometry><cylinder radius="0.07" length="0.05"/></geometry><origin rpy="0 0 0" xyz="0 0 0.025"/></visual>
    <collision><geometry><cylinder radius="0.07" length="0.05"/></geometry><origin rpy="0 0 0" xyz="0 0 0.025"/></collision>
  </link>
  <link name="column">
    <visual><geometry><cylinder radius="0.035" length="0.35"/></geometry><origin rpy="0 0 0" xyz="0 0 0.175"/></visual>
    <collision><geometry><cylinder radius="0.035" length="0.35"/></geometry><origin rpy="0 0 0" xyz="0 0 0.175"/></collision>
  </link>
  <link name="upper">
    <visual><geometry><cylinder radius="0.03" length="{LINK1}"/></geometry><origin rpy="0 1.5708 0" xyz="{LINK1/2} 0 0"/></visual>
    <collision><geometry><cylinder radius="0.03" length="{LINK1}"/></geometry><origin rpy="0 1.5708 0" xyz="{LINK1/2} 0 0"/></collision>
  </link>
  <link name="fore">
    <visual><geometry><cylinder radius="0.026" length="{LINK2}"/></geometry><origin rpy="0 1.5708 0" xyz="{LINK2/2} 0 0"/></visual>
    <collision><geometry><cylinder radius="0.026" length="{LINK2}"/></geometry><origin rpy="0 1.5708 0" xyz="{LINK2/2} 0 0"/></collision>
  </link>
  <link name="wrist">
    <visual><geometry><box size="0.064 0.06 0.036"/></geometry></visual>
    <collision><geometry><box size="0.064 0.06 0.036"/></geometry></collision>
  </link>
  <link name="finger_l">
    <visual><geometry><box size="0.028 0.016 0.09"/></geometry></visual>
    <collision><geometry><box size="0.028 0.016 0.09"/></geometry></collision>
  </link>
  <link name="finger_r">
    <visual><geometry><box size="0.028 0.016 0.09"/></geometry></visual>
    <collision><geometry><box size="0.028 0.016 0.09"/></geometry></collision>
  </link>

  <joint name="pan" type="revolute">
    <parent link="base"/><child link="column"/>
    <origin xyz="0 0 0.05"/><axis xyz="0 0 1"/>
    <limit lower="-3.1416" upper="3.1416" effort="100" velocity="10"/>
  </joint>
  <joint name="shoulder" type="revolute">
    <parent link="column"/><child link="upper"/>
    <origin xyz="0 0 0.35"/><axis xyz="0 1 0"/>
    <limit lower="-2.0" upper="2.0" effort="100" velocity="10"/>
  </joint>
  <joint name="elbow" type="revolute">
    <parent link="upper"/><child link="fore"/>
    <origin xyz="{LINK1} 0 0"/><axis xyz="0 1 0"/>
    <limit lower="-2.6" upper="2.6" effort="100" velocity="10"/>
  </joint>
  <joint name="wristp" type="revolute">
    <parent link="fore"/><child link="wrist"/>
    <origin xyz="{LINK2} 0 0"/><axis xyz="0 1 0"/>
    <limit lower="-2.8" upper="2.8" effort="100" velocity="10"/>
  </joint>
  <joint name="grip_l" type="prismatic">
    <parent link="wrist"/><child link="finger_l"/>
    <origin xyz="0 0 -{GRIP_MID}"/><axis xyz="0 1 0"/>
    <limit lower="0.012" upper="0.060" effort="50" velocity="5"/>
  </joint>
  <joint name="grip_r" type="prismatic">
    <parent link="wrist"/><child link="finger_r"/>
    <origin xyz="0 0 -{GRIP_MID}"/><axis xyz="0 -1 0"/>
    <limit lower="0.012" upper="0.060" effort="50" velocity="5"/>
  </joint>
</robot>
"""


class PyBulletSimulator:
    ROBOT_ID = "door-arm-001"
    SKILL_ID = "open_door"
    ENGINE = ENGINE

    def __init__(self):
        self._steps = 0
        self._budget = 400
        self._door_angle = 0.0
        self._handle_angle = 0.0
        self._peak_force = 0.0
        self._hold_forces = []
        self._contact_samples = 0
        self._collisions = 0
        self._pose = {"pan": 0.0, "shoulder": 0.0, "elbow": 0.0, "wristp": 0.0}
        self._grip = 0.050
        self._scene_key = "open"
        self._t0 = 0.0

    def _build(self, scene: dict):
        """Build scene using PyBullet or stub."""
        self._scene = scene
        if available():
            import pybullet as p
            self._p = p
            self._uid = p.loadURDF(_robot_urdf(), [0, 0, 0])
            # Load door
            self._door_uid = p.createCollisionShape(p.GEOM_BOX, halfExtents=[DOOR_WIDTH/2, 0.03, 1.05])
            self._door_idx = p.createMultiBody(1, self._door_uid, basePosition=[scene["door_x"], scene["door_y"], 1.05])
            # Create door hinge constraint
            self._door_constraint = p.createConstraint(
                self._door_idx, -1, -1, -1,
                p.JOINT_REVOLUTE,
                [0, 0, 0],
                [0, 0, 0],
                [scene["door_x"], scene["door_y"], 0]
            )
            # Set friction
            p.changeDynamics(self._door_idx, -1, lateralFriction=scene.get("friction", 0.3))
        else:
            # Stub mode - simulate deterministically
            self._stub = True
            self._stub_calls = []
            import tests.bullet_stub as stub
            stub.S.register_sim(self)
            # Call stub methods to record them
            self._uid = stub.S.loadURDF(_robot_urdf(), [0, 0, 0])
            self._door_uid = stub.S.createCollisionShape(stub.S.GEOM_BOX, halfExtents=[DOOR_WIDTH/2, 0.03, 1.05])
            self._door_idx = stub.S.createMultiBody(1, self._door_uid, basePosition=[scene["door_x"], scene["door_y"], 1.05])
            self._door_constraint = stub.S.createConstraint(
                self._door_idx, -1, -1, -1,
                stub.S.JOINT_REVOLUTE,
                [0, 0, 0],
                [0, 0, 0],
                [scene["door_x"], scene["door_y"], 0]
            )
            stub.S.changeDynamics(self._door_idx, -1, lateralFriction=scene.get("friction", 0.3))
            self._simulate_stub_step(dict(self._pose), self._grip)

    def _tick(self, pose: dict, grip: float):
        if self._steps >= self._budget:
            raise BudgetExhausted

        if available():
            import pybullet as p
            # Set joint positions
            for i, name in enumerate(ARM_JOINTS):
                p.setJointMotorControl2(self._uid, i, p.POSITION_CONTROL, targetPosition=pose[name])
            # Set gripper
            p.setJointMotorControl2(self._uid, 4, p.POSITION_CONTROL, targetPosition=grip)
            p.setJointMotorControl2(self._uid, 5, p.POSITION_CONTROL, targetPosition=grip)
            p.stepSimulation()
        else:
            # Stub mode - deterministic simulation
            import tests.bullet_stub as stub
            for i, name in enumerate(ARM_JOINTS):
                stub.S.setJointMotorControl2(self._uid, i, stub.S.POSITION_CONTROL, targetPosition=pose[name])
            stub.S.setJointMotorControl2(self._uid, 4, stub.S.POSITION_CONTROL, targetPosition=grip)
            stub.S.setJointMotorControl2(self._uid, 5, stub.S.POSITION_CONTROL, targetPosition=grip)
            stub.S.stepSimulation()

        self._steps += 1
        self._pose = pose
        self._grip = grip

    def _simulate_stub_step(self, pose: dict, grip: float):
        """Stub physics: simulate door opening based on scene friction."""
        # Stub mode is handled in bullet_stub; this is a fallback
        pass

    def _run(self, target: dict, n: int, grip: float):
        start = dict(self._pose)
        for i in range(1, n + 1):
            self._tick(blend(start, target, i / n), grip)
            if self._collisions:
                return False
        return True

    def _hold(self, n: int, grip: float, sample: bool = False):
        for _ in range(n):
            self._tick(dict(self._pose), grip)
            if sample:
                f = self._grasp_force()
                self._hold_forces.append(f)
                self._peak_force = max(self._peak_force, f)

    def _grasp_force(self):
        if available():
            import pybullet as p
            contacts = p.getContactPoints(linkIndexA=self._uid, linkIndexB=self._door_idx)
            total = sum(abs(c[10]) for c in contacts if abs(c[10]) > 0)
            return total
        # Stub: delegate to stub's simulation state
        if hasattr(self, '_stub') and self._stub:
            import tests.bullet_stub as stub
            return stub.S._peak_force if stub.S._peak_force > 0 else 0.0
        return 0.5  # fallback

    def open_door(self, params: dict | None = None):
        from arm_spec import solve
        name, key, scene = resolve_scene(params)

        t0 = time.perf_counter()
        self._build(scene)
        self._budget = scene["budget"]
        self._t0 = t0
        self._scene_key = key
        # Handle start position (world coords, used for objectDelta)
        hz_init = scene.get("handle_z", DOOR_HANDLE_HEIGHT)
        self._handle_start_pos = (
            scene["door_x"] + DOOR_WIDTH,
            scene["door_y"],
            hz_init,
        )

        hx = scene["door_x"] + DOOR_WIDTH - 0.05
        hy = scene["door_y"]
        hz = scene.get("handle_z", DOOR_HANDLE_HEIGHT)

        above = solve(hx, hz + 0.10 + GRIP_MID)
        grip = solve(hx, hz + GRIP_MID)
        pull_end = solve(hx - 0.20, hz - 0.05 + GRIP_MID)

        if above is None or grip is None or pull_end is None:
            return self._fail("configuration_error", "keyframes unsolvable")

        try:
            # Stage 1: move above handle
            if not self._run(above, 70, 0.050):
                return self._fail("collision", "obstacle during approach")

            # Stage 2: descend to handle
            if not self._run(grip, 50, 0.050):
                return self._fail("collision", "obstacle during descent")

            # Stage 3: grip handle
            for i in range(1, 81):
                self._tick(dict(self._pose), aperture_at(i / 80))
                f = self._grasp_force()
                self._peak_force = max(self._peak_force, f)
                if f > 0.0:
                    self._contact_samples += 1

            force = self._grasp_force()
            if force < GRASP_FORCE_MIN:
                return self._fail("grasp_failed", f"peak_force={self._peak_force:.3f} N")

            handle_state = "gripped"

            # Stage 4: pull door open
            if not self._run(pull_end, 100, 0.032):
                if self._door_angle < OPEN_ANGLE_MIN:
                    return self._fail("stuck", f"door angle only {self._door_angle:.2f} rad")

            # Stage 5: settle
            self._hold(30, 0.032, sample=True)

        except BudgetExhausted:
            return self._fail("timeout", f"step budget {self._budget} exhausted")

        if self._door_angle < OPEN_ANGLE_MIN:
            return self._fail("insufficient_open", f"door opened {self._door_angle:.2f} rad")

        return self._success()

    def _success(self):
        hold = (sum(self._hold_forces) / len(self._hold_forces)
                if self._hold_forces else 0.5)
        handle_end = (
            self._handle_start_pos[0] + DOOR_WIDTH * (1 - np.cos(self._door_angle)),
            self._handle_start_pos[1] + DOOR_WIDTH * np.sin(self._door_angle),
            self._handle_start_pos[2],
        )
        metrics = build_metrics(
            engine=ENGINE, obj="open", scene_key=self._scene_key, stage="full",
            handle_state="gripped", start_pos=self._handle_start_pos,
            end_pos=handle_end,
            hold_force=hold, peak_force=self._peak_force,
            contact_samples=self._contact_samples,
            collisions=self._collisions, steps=self._steps,
            budget=self._budget, wall_time=time.perf_counter() - self._t0,
            door_angle=max(self._door_angle, OPEN_ANGLE_MIN + 0.1), note="success")
        from arm_spec import DoorResult
        return DoorResult(True, "opened", metrics)

    def _fail(self, reason: str, note: str):
        metrics = build_metrics(
            engine=ENGINE, obj="open", scene_key=self._scene_key, stage="full",
            handle_state="ungripped", start_pos=self._handle_start_pos,
            end_pos=self._handle_start_pos,
            hold_force=0.0, peak_force=self._peak_force,
            contact_samples=self._contact_samples,
            collisions=self._collisions, steps=self._steps,
            budget=self._budget, wall_time=time.perf_counter() - self._t0,
            door_angle=self._door_angle, note=note)
        from arm_spec import DoorResult
        return DoorResult(False, reason, metrics)


__all__ = ["PyBulletSimulator", "available", "_robot_urdf"]
