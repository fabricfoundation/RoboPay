"""Paired MuJoCo/Webots validation for one shared G1 policy contract."""

from __future__ import annotations

from .runner import run_inspection
from .webots import run_webots_validation


def _shared(result: dict) -> dict:
    state = result.get("policy_state", {})
    return {
        "policy_id": result.get("policy_id") or state.get("policy_id"),
        "targets": result.get("targets_requested"),
        "parameters": state.get("parameters"),
        "support_fixture": result.get("support_fixture"),
    }


def run_sim2sim_validation(timeout_seconds: int = 300) -> dict:
    mujoco_result = run_inspection()
    webots_result = run_webots_validation(timeout_seconds)
    shared_match = _shared(mujoco_result) == _shared(webots_result)
    confirmations = len(set(mujoco_result.get("targets_confirmed", ())) & set(webots_result.get("targets_confirmed", ())))
    score = confirmations / 3.0
    success = bool(mujoco_result.get("success")) and bool(webots_result.get("success")) and shared_match and score == 1.0
    return {
        "task": "inspect_target_sequence_sim2sim",
        "status": "success" if success else "failure",
        "success": success,
        "sim_to_sim_score": round(score, 3),
        "shared_policy_match": shared_match,
        "shared_policy": _shared(mujoco_result),
        "mujoco": mujoco_result,
        "webots": webots_result,
        "note": "Both engines execute the same feedback target sequencer; only torque/position actuator adapters differ.",
    }
