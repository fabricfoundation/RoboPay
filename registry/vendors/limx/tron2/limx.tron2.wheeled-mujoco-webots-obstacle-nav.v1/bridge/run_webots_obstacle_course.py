from __future__ import annotations

import argparse
import json

from limx_tron2_sim.contracts import NAVIGATION_SKILL, NavigationRequest
from limx_tron2_sim.webots import run_webots_episode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer", action="store_true")
    args = parser.parse_args()
    result = run_webots_episode(NavigationRequest(NAVIGATION_SKILL), viewer=args.viewer)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
