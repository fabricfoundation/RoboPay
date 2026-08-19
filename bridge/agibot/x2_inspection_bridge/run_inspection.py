from x2_inspection_bridge.runner import run_inspection

if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Run the AGIBot X2 Tier 1 inspection task.")
    parser.add_argument("--model-dir")
    parser.add_argument("--max-duration", type=float, default=18.0)
    parser.add_argument("--targets", nargs="+", choices=("left", "center", "right"), default=("left", "center", "right"))
    parser.add_argument("--speed-scale", type=float, default=1.0)
    parser.add_argument("--viewer", action="store_true")
    parser.add_argument("--playback-rate", type=float, default=1.0)
    parser.add_argument("--viewer-hold-seconds", type=float)
    parser.add_argument("--viewer-target-hold-seconds", type=float, default=0.0)
    parser.add_argument("--viewer-start-hold-seconds", type=float, default=0.0)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    result = run_inspection(
        args.model_dir, args.max_duration, tuple(args.targets), args.speed_scale,
        args.viewer, args.playback_rate, args.viewer_hold_seconds,
        args.viewer_target_hold_seconds, args.viewer_start_hold_seconds,
    )
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)
