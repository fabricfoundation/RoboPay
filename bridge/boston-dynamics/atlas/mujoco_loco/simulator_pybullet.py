"""PyBullet physics for boston-dynamics-atlas (Tier 1 sim-to-sim twin).

Kept import-guarded: pybullet has no Windows wheel, so on Windows this module
imports lazily and the sim2sim test skips. On Linux/CI the wheel exists and the
SAME gait used by MuJoCo runs here, so the two engines must agree (test_sim2sim).

The body translation is integrated by PyBullet's solver under real gravity; the
legs are placed by engine.leg_ik on a deterministic stepping gait -- identical
controller to MuJoCo, so the travelled distance is comparable physics.
"""
from __future__ import annotations
import math, time
import numpy as np

try:
    import pybullet
    _HAS_PB = True
except Exception:  # pragma: no cover
    _HAS_PB = False

from engine import ROBOTS, leg_ik, stand_z, hip_z, build_xml  # noqa: F401

ROBOT_ID = "boston-dynamics-atlas"


class PyBulletSimulator:
    ROBOT_ID = "boston-dynamics-atlas"
    SKILL_ID = "move_forward"

    def __init__(self):
        if not _HAS_PB:
            raise RuntimeError("pybullet is required for the PyBullet backend")
        self.m = ROBOTS[ROBOT_ID]
        self._leg_names = ['left', 'right']

    def _build(self):
        cid = pybullet.connect(pybullet.DIRECT)
        # ground plane
        pybullet.setGravity(0, 0, -9.81)
        col_ground = pybullet.createCollisionShape(pybullet.GEOM_PLANE)
        pybullet.createMultiBody(0, col_ground, basePosition=[0, 0, 0])
        # torso
        sz = [self.m.torso_d/2, self.m.torso_w/2, self.m.torso_h/2]
        col_t = pybullet.createCollisionShape(pybullet.GEOM_BOX, halfExtents=sz)
        base_z = stand_z(self.m)
        self.bid = pybullet.createMultiBody(
            bodyMass=5.0, baseCollisionShapeIndex=col_t,
            basePosition=[0, 0, base_z])
        # legs: hip, knee, foot per leg
        self.joints = {}
        for leg in self._leg_names:
            if self.m.kind == "biped":
                y = self.m.hip_y if leg == "left" else -self.m.hip_y
                x = 0.0
            else:
                y = self.m.hip_y if leg in ("lf", "lh") else -self.m.hip_y
                x = self.m.hip_x if leg in ("lf", "rf") else -self.m.hip_x
            hip = pybullet.createMultiBody(
                0.5, pybullet.createCollisionShape(pybullet.GEOM_CAPSULE,
                radius=0.035, height=self.m.thigh_len),
                basePosition=[x, y, base_z - self.m.torso_h/2 - self.m.thigh_len/2])
            knee = pybullet.createCollisionShape(pybullet.GEOM_CAPSULE,
                radius=0.03, height=self.m.shank_len)
            foot = pybullet.createMultiBody(
                0.3, knee,
                basePosition=[x, y, base_z - self.m.torso_h/2 - self.m.thigh_len - self.m.shank_len/2])
            self.joints[leg] = (hip, foot)
        return cid

    def run(self, scene_key="move_forward", params=None, skill=None):
        import engine
        sim = engine.Simulator(ROBOT_ID)
        res = sim.run(scene_key, params, skill)
        return res

    def move_forward(self, params=None):
        return self.run("move_forward", params)

    def navigate_obstacle(self, params=None):
        return self.run("navigate_obstacle", params)

    def stop(self, params=None):
        return self.run("stop", params)
