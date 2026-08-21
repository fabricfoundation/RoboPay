"""Compare the same Atlas DRC state-feedback contract in MuJoCo and Webots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas_drc_bridge.contracts import validate_wave_params
from atlas_drc_bridge.runtime import run_wave_episode
from atlas_drc_bridge.webots import run_webots_validation


PACKAGE_ROOT = Path(__file__).resolve().parent


def run_sim2sim_validation(timeout_seconds: int = 60) -> dict:
    params = validate_wave_params({"cycles": 2, "amplitudeRad": 0.30, "maxDurationSec": 8.0})
    mujoco_result = run_wave_episode(params)
    webots_result = run_webots_validation(timeout_seconds)
    shared = {
        "policy_id": "atlas-drc-right-arm-wave-v1",
        "skill_id": "wave_right_arm",
        "cycles": params.cycles,
        "amplitude_rad": params.amplitude_rad,
        "max_duration_sec": params.max_duration_sec,
        "state_authority": "measured right-shoulder joint position",
    }
    comparison = {
        "both_engines_succeeded": bool(mujoco_result.get("success"))
        and bool(webots_result.get("success")),
        "policy_id_match": mujoco_result.get("policy_id") == webots_result.get("policy_id"),
        "completed_half_waves_match": mujoco_result.get("completed_half_waves")
        == webots_result.get("completed_half_waves")
        == params.cycles * 2,
        "measured_stroke_threshold_met": mujoco_result.get("measured_wave_stroke_rad", 0)
        >= params.amplitude_rad * 1.35
        and webots_result.get("measured_wave_stroke_rad", 0)
        >= params.amplitude_rad * 1.35,
        "webots_base_stable": bool(webots_result.get("stable_base")),
    }
    sim_to_sim_score = sum(comparison.values()) / len(comparison)
    success = sim_to_sim_score == 1.0
    return {
        "task": "atlas_drc_right_arm_wave_sim2sim",
        "status": "success" if success else "failure",
        "success": success,
        "shared_policy": shared,
        "comparison": comparison,
        "sim_to_sim_score": sim_to_sim_score,
        "mujoco": mujoco_result,
        "webots": webots_result,
        "note": (
            "The simulators use independently supplied legacy Atlas DRC models. "
            "This is cross-engine behavior validation, not a claim that either is the current electric Atlas."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Atlas DRC MuJoCo/Webots Sim-to-Sim validation.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=PACKAGE_ROOT / "artifacts" / "sim2sim_result.json",
    )
    args = parser.parse_args()
    result = run_sim2sim_validation(args.timeout)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
