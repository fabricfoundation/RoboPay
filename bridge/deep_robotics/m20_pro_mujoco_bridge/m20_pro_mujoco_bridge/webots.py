"""Execute the generated M20 PROTO in a real Webots process."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .contracts import DriveRequest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCENE = PACKAGE_ROOT / "scenes" / "m20_obstacle_course.wbt"
CONFIG = PACKAGE_ROOT / "scenes" / "m20_webots_config.json"
RESULT = PACKAGE_ROOT / "scenes" / "m20_webots_result.json"


def _webots_executable(configured: str | None = None) -> str:
    candidate = configured or os.environ.get("WEBOTS_EXE") or shutil.which("webots")
    if not candidate:
        raise FileNotFoundError("Webots R2025a is required; set WEBOTS_EXE when it is not on PATH")
    return candidate


def run_webots_episode(
    request: DriveRequest,
    *,
    webots_executable: str | None = None,
    viewer: bool = False,
) -> dict[str, Any]:
    """Generate from the vendor URDF, run Webots, and return its own result."""

    from generate_webots_proto import generate

    generate()
    CONFIG.write_text(
        json.dumps(
            {
                "goal_distance_m": request.goal_distance_m,
                "wheel_speed_rad_s": request.wheel_speed_rad_s,
                "max_duration_sec": request.max_duration_sec,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    RESULT.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["M20_WEBOTS_CONFIG_PATH"] = str(CONFIG)
    environment["M20_WEBOTS_RESULT_PATH"] = str(RESULT)
    command = [_webots_executable(webots_executable)]
    if viewer:
        environment.setdefault("M20_WEBOTS_VIEWER_HOLD_SECONDS", "300")
        command.extend(["--mode=realtime", "--stdout", "--stderr"])
    else:
        command.extend(["--batch", "--mode=fast", "--no-rendering", "--stdout", "--stderr"])
    command.append(str(SCENE))
    completed = subprocess.run(command, env=environment, capture_output=True, text=True, check=False)
    if not RESULT.is_file():
        raise RuntimeError(
            "Webots did not produce its measured result JSON. "
            f"returncode={completed.returncode}; stderr={completed.stderr[-1000:]}"
        )
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    result["webots_return_code"] = completed.returncode
    if completed.returncode != 0:
        result["success"] = False
        result["status"] = "failure"
        result.setdefault("completion_reason", "webots_process_failure")
    return result
