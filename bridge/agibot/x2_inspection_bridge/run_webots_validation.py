from __future__ import annotations

import argparse
import json

from x2_inspection_bridge.webots import launch_webots_viewer, run_webots_validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--viewer", action="store_true")
    args = parser.parse_args()
    if args.viewer:
        print(f"Webots viewer started (PID {launch_webots_viewer()}).")
    else:
        result = run_webots_validation(args.timeout)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result.get("success") else 1)
