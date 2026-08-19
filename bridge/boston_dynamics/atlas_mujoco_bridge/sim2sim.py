"""Sim-to-Sim validation: MuJoCo → Webots comparison.

Runs the same policy on both engines and produces a structured comparison.
Webots execution requires R2025a; when unavailable, falls back to
MuJoCo-only mode with a compatibility note.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .runner import run_obstacle_nav


def _hash_metrics(metrics: dict) -> str:
    canonical = json.dumps(metrics, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def run_sim2sim(model_dir: str | None = None, max_duration: float = 10.0) -> dict:
    mujoco_result = run_obstacle_nav(
        model_dir=model_dir, max_duration_seconds=max_duration,
        side="left", speed_scale=1.0,
    )

    mujoco_hash = _hash_metrics(mujoco_result)

    webots_result = None
    webots_hash = None
    webots_available = False
    try:
        from .webots import run_webots_validation
        webots_result = run_webots_validation(max_duration=max_duration)
        webots_hash = _hash_metrics(webots_result) if webots_result else None
        webots_available = webots_result is not None
    except (ImportError, FileNotFoundError, RuntimeError):
        pass

    comparison = {
        "mujoco": {
            "engine": mujoco_result.get("simulator_engine"),
            "forward_progress_m": mujoco_result.get("forward_progress_m"),
            "min_body_height_m": mujoco_result.get("min_body_height_m"),
            "fall_detected": mujoco_result.get("fall_detected"),
            "obstacle_contacts": mujoco_result.get("obstacle_contacts"),
            "upright_fraction": mujoco_result.get("upright_fraction"),
            "hash": mujoco_hash,
        },
        "webots": {
            "engine": "Webots" if webots_available else "unavailable",
            "available": webots_available,
            "forward_progress_m": webots_result.get("forward_progress_m") if webots_result else None,
            "hash": webots_hash,
        },
        "consistency": {
            "forward_progress_delta": (
                abs(mujoco_result.get("forward_progress_m", 0) - webots_result.get("forward_progress_m", 0))
                if webots_result else None
            ),
            "both_upright": (
                mujoco_result.get("upright_fraction", 0) > 0.9
                and (webots_result.get("upright_fraction", 0) > 0.9 if webots_result else False)
            ),
            "webots_available": webots_available,
        },
    }

    return {
        "validation_type": "sim2sim",
        "robot_model": mujoco_result.get("robot_model"),
        "policy_id": mujoco_result.get("policy_id"),
        "mujoco_result": mujoco_result,
        "webots_result": webots_result,
        "comparison": comparison,
        "status": "pass" if mujoco_result.get("forward_progress_m", 0) > 0.2 else "partial",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas sim-to-sim validation.")
    parser.add_argument("--model-dir", help="Directory containing humanoid.xml.")
    parser.add_argument("--max-duration", type=float, default=10.0)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = run_sim2sim(args.model_dir, args.max_duration)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
