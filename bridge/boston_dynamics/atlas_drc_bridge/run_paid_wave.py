"""Run the bounded Atlas DRC MuJoCo wave and write reviewable metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas_drc_bridge.contracts import validate_wave_params
from atlas_drc_bridge.runtime import run_wave_episode


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Boston Dynamics Atlas DRC v4 wave in MuJoCo.")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--amplitude-rad", type=float, default=0.30)
    parser.add_argument("--max-duration", type=float, default=8.0)
    parser.add_argument("--model-dir")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--viewer",
        action="store_true",
        help="Open the real MuJoCo desktop viewer; intended for an operator recording a run.",
    )
    parser.add_argument(
        "--viewer-hold-seconds",
        type=float,
        default=3.0,
        help="Keep the terminal MuJoCo pose visible after a --viewer run.",
    )
    parser.add_argument("--viewer-start-hold-seconds", type=float, default=0.0)
    parser.add_argument("--viewer-turn-hold-seconds", type=float, default=0.0)
    args = parser.parse_args()
    params = validate_wave_params(
        {"cycles": args.cycles, "amplitudeRad": args.amplitude_rad, "maxDurationSec": args.max_duration}
    )
    result = run_wave_episode(
        params,
        model_dir=args.model_dir,
        viewer=args.viewer,
        viewer_hold_seconds=max(0.0, args.viewer_hold_seconds),
        viewer_start_hold_seconds=max(0.0, args.viewer_start_hold_seconds),
        viewer_turn_hold_seconds=max(0.0, args.viewer_turn_hold_seconds),
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
