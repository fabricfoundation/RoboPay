"""Skill dispatch: turn a paid action into an actionId-correlated terminal result.

This is the robot-side execution step. It runs the real closed-loop MuJoCo
servo (SimArm01Simulator) and reports a terminal result whose status reflects
what physically happened — success only when the arm actually reached the pose.
"""
from .envelope import ActionEnvelope, ResultEnvelope
from ..simulator import SimArm01Simulator

ROBOT_ID = "sim-arm-01"


def execute_skill(action: ActionEnvelope, sim: SimArm01Simulator = None) -> ResultEnvelope:
    """Dispatch a skill to its controller and return a terminal ResultEnvelope."""
    if action.skillId != "move_to_pose":
        return ResultEnvelope.error(
            action, "UNKNOWN_SKILL", f"no skill named {action.skillId!r}")

    target = action.params.get("target_qpos")
    if not (isinstance(target, list) and len(target) == 2
            and all(isinstance(v, (int, float)) for v in target)):
        return ResultEnvelope.error(
            action, "INVALID_PARAMS",
            "target_qpos must be a list of 2 numeric joint angles")

    sim = sim or SimArm01Simulator()
    metrics = sim.execute(target)      # real simulator state, not a replay

    if metrics.get("success"):
        return ResultEnvelope.success(action, metrics)
    return ResultEnvelope.error(
        action, "ACTION_FAILED",
        "arm did not reach target within step budget", metrics)
