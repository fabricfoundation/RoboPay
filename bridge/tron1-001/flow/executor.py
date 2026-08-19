"""Skill execution interface + executors (planar biped, Tier 1).

SkillExecutor is the seam the relay depends on. D1 used MockExecutor (no robot).
D3 plugs in real physics. D4 makes the physics engine itself swappable, which
is what keeps the robot adapter replaceable: payment / relay / transport code
never learns which simulator (or, later, which real robot) is underneath.

Backends are imported lazily so a missing optional engine can never break the
payment path.

The three planar-biped locomotion skills -- move_forward / navigate_obstacle /
stop -- all run on the same simulator; SimExecutor just dispatches by skill id
and returns the engine-agnostic SkillResult the relay expects.
"""
from __future__ import annotations

from tron1_spec import SCENES


class SkillResult:
    def __init__(self, success: bool, message: str, metrics: dict | None = None):
        self.success = success
        self.message = message
        self.metrics = metrics or {}

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "message": self.message,
            "metrics": self.metrics,
        }


class SkillExecutor:
    def execute(self, skill_id: str, params: dict) -> SkillResult:
        raise NotImplementedError


class MockExecutor(SkillExecutor):
    """D1 stand-in. No physics. Counts executions so tests prove no double-run.

    Faithful to the paid flow: a supported skill is reported as completed, an
    unsupported one is rejected (never settles, never double-runs).
    """

    def __init__(self, fail_skill: str | None = None):
        self.fail_skill = fail_skill
        self.execution_count = 0

    def execute(self, skill_id: str, params: dict) -> SkillResult:
        self.execution_count += 1
        if skill_id not in SCENES:
            return SkillResult(False, f"unsupported_skill:{skill_id}")
        if skill_id == self.fail_skill:
            return SkillResult(False, f"failed:{skill_id}")
        return SkillResult(True, f"{skill_id}: moved (mock)")


BACKENDS = ("mujoco", "pybullet")


def make_simulator(engine: str = "mujoco"):
    """Robot adapter factory. Adding a real robot means adding a branch here
    and nothing else."""
    if engine == "mujoco":
        from simulator import MuJoCoSimulator
        return MuJoCoSimulator()
    if engine == "pybullet":
        from simulator_pybullet import PyBulletSimulator
        return PyBulletSimulator()
    raise ValueError(f"unknown engine: {engine!r} (expected one of {BACKENDS})")


class SimExecutor(SkillExecutor):
    """Real Tier 1 executor: physics-backed locomotion on tron1-001."""

    def __init__(self, engine: str = "mujoco"):
        self.engine = engine
        self.sim = make_simulator(engine)
        self.supported = set(SCENES)

    def execute(self, skill_id: str, params: dict) -> SkillResult:
        if skill_id not in self.supported:
            return SkillResult(False, f"unsupported_skill:{skill_id}")
        method = getattr(self.sim, skill_id, None)
        if method is None:
            return SkillResult(False, f"unsupported_skill:{skill_id}")
        # The simulator resolves the scene from (params, skill_id) and returns
        # a WalkResult; we surface it as the engine-agnostic SkillResult.
        res = method(params or {})
        return SkillResult(res.success, res.message, res.metrics)


class MuJoCoExecutor(SimExecutor):
    """Default backend, kept as a named type for readability in the bridge."""

    def __init__(self):
        super().__init__("mujoco")
