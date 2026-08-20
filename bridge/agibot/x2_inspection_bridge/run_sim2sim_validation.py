from __future__ import annotations

import argparse
import json
from pathlib import Path

from x2_inspection_bridge.sim2sim import run_sim2sim_validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--json-output", type=Path, default=Path(__file__).resolve().parent / "artifacts" / "sim2sim_result.json")
    args = parser.parse_args()
    result = run_sim2sim_validation(args.timeout)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result["success"] else 1)
