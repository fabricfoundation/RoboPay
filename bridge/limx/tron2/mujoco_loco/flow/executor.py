"""Skill execution interface + executors (limx-tron2, Tier 1).

Mirrors the G1 SimExecutor but is engine-backed for limx-tron2. The relay
never learns which simulator is underneath; adding a robot means swapping the
morphology in engine.py and nothing else.
"""
from __future__ import annotations
from engine import ROBOTS, Simulator

ROBOT_ID = "limx-tron2"
SCENES = ROBOTS[ROBOT_ID].scenes


class SkillResult:
    def __init__(self, success, message, metrics=None):
        self.success = success
        self.message = message
        self.metrics = metrics or {}

    def to_dict(self):
        return {"success": self.success, "message": self.message,
                "metrics": self.metrics}


class SkillExecutor:
    def execute(self, skill_id, params):
        raise NotImplementedError


class MockExecutor(SkillExecutor):
    def __init__(self, fail_skill=None):
        self.fail_skill = fail_skill
        self.execution_count = 0

    def execute(self, skill_id, params):
        self.execution_count += 1
        if skill_id not in SCENES:
            return SkillResult(False, f"unsupported_skill:{skill_id}")
        if skill_id == self.fail_skill:
            return SkillResult(False, f"failed:{skill_id}")
        return SkillResult(True, f"{skill_id}: moved (mock)")


BACKENDS = ("mujoco", "pybullet")


def make_simulator(engine_name="mujoco"):
    if engine_name == "mujoco":
        from simulator import MuJoCoSimulator
        return MuJoCoSimulator()
    if engine_name == "pybullet":
        from simulator_pybullet import PyBulletSimulator
        return PyBulletSimulator()
    raise ValueError(f"unknown engine: {engine_name!r}")


class SimExecutor(SkillExecutor):
    """Real Tier 1 executor: physics-backed locomotion on limx-tron2."""

    def __init__(self, engine_name="mujoco"):
        self.engine = engine_name
        self.sim = make_simulator(engine_name)
        self.supported = set(SCENES)

    def execute(self, skill_id, params):
        if skill_id not in self.supported:
            return SkillResult(False, f"unsupported_skill:{skill_id}")
        method = getattr(self.sim, skill_id, None)
        if method is None:
            return SkillResult(False, f"unsupported_skill:{skill_id}")
        res = method(params or {})
        return SkillResult(res.success, res.message, res.metrics)


class MuJoCoExecutor(SimExecutor):
    def __init__(self):
        super().__init__("mujoco")
