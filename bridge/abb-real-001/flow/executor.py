"""Skill execution interface + executors.

SkillExecutor is the seam the relay depends on. D1 used MockExecutor (no robot).
D3 plugs in real physics. D4 makes the physics engine itself swappable, which
is what keeps the robot adapter replaceable: payment / relay / transport code
never learns which simulator (or, later, which real robot) is underneath.

Backends are imported lazily so a missing optional engine can never break the
payment path.
"""


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
    """D1 stand-in. No physics. Counts executions so tests prove no double-run."""

    def __init__(self, fail_skill: str | None = None):
        self.fail_skill = fail_skill
        self.execution_count = 0

    def execute(self, skill_id: str, params: dict) -> SkillResult:
        self.execution_count += 1
        if skill_id == self.fail_skill or (params or {}).get("object") == "unreachable":
            return SkillResult(False, "unreachable")
        return SkillResult(True, "cube moved")


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
    """Real Tier 1 executor: physics-backed `pick_object` on abb-real-001."""

    def __init__(self, engine: str = "mujoco"):
        self.engine = engine
        self.sim = make_simulator(engine)
        self.supported = {"pick_object"}

    def execute(self, skill_id: str, params: dict) -> SkillResult:
        if skill_id not in self.supported:
            return SkillResult(False, f"unsupported_skill:{skill_id}")
        res = self.sim.pick_object(params or {})
        return SkillResult(res.success, res.reason, res.metrics)


class MuJoCoExecutor(SimExecutor):
    """Default backend, kept as a named type for readability in the bridge."""

    def __init__(self):
        super().__init__("mujoco")
