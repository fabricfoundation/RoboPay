"""Execute the generated X30 PROTO in a real Webots process."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .course import fingerprint as course_fingerprint
from .course import spec as course_spec
from .course import webots_blockers_vrml
from .course import webots_finish_marker_vrml
from .contracts import DriveRequest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCENE_TEMPLATE = PACKAGE_ROOT / "scenes" / "x30_inspection_course.wbt"
GENERATED_SCENE = PACKAGE_ROOT / "scenes" / "x30_inspection_course.generated.wbt"
CONFIG = PACKAGE_ROOT / "scenes" / "x30_webots_config.json"
RESULT = PACKAGE_ROOT / "scenes" / "x30_webots_result.json"


def _webots_executable(configured: str | None = None) -> str:
    candidate = configured or os.environ.get("WEBOTS_EXE") or shutil.which("webots")
    if not candidate:
        raise FileNotFoundError("Webots R2025a is required; set WEBOTS_EXE when it is not on PATH")
    return candidate


def _render_course_scene() -> Path:
    """Render static Webots collision bodies from the canonical course spec."""

    text = SCENE_TEMPLATE.read_text(encoding="utf-8")
    regions = (
        (
            "# BEGIN PROFILE_COURSE_BLOCKERS (rendered from x30_pro_mujoco_bridge.course)",
            "# END PROFILE_COURSE_BLOCKERS",
            webots_blockers_vrml(),
        ),
        (
            "# BEGIN PROFILE_FINISH_MARKER (rendered from x30_pro_mujoco_bridge.course)",
            "# END PROFILE_FINISH_MARKER",
            webots_finish_marker_vrml(),
        ),
    )
    for begin, end, rendered in regions:
        if text.count(begin) != 1 or text.count(end) != 1:
            raise RuntimeError("X30 Webots course template markers are missing or ambiguous")
        prefix, remaining = text.split(begin, 1)
        _, suffix = remaining.split(end, 1)
        text = f"{prefix}{begin}\n{rendered}\n{end}{suffix}"
    GENERATED_SCENE.write_text(
        text, encoding="utf-8"
    )
    return GENERATED_SCENE


def run_webots_episode(
    request: DriveRequest,
    *,
    webots_executable: str | None = None,
    viewer: bool = False,
) -> dict[str, Any]:
    """Generate from the vendor URDF, run Webots, and return its own result."""

    from generate_webots_proto import generate

    generate()
    scene = _render_course_scene()
    CONFIG.write_text(
        json.dumps(
            {
                "route": "inspection-lane-v1",
                "gait_cycles": request.gait_cycles,
                "hip_sweep_rad": request.hip_sweep_rad,
                "max_duration_sec": request.max_duration_sec,
                "course": course_spec(),
                "course_hash": course_fingerprint(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    RESULT.unlink(missing_ok=True)
    environment = os.environ.copy()
    environment["X30_WEBOTS_CONFIG_PATH"] = str(CONFIG)
    environment["X30_WEBOTS_RESULT_PATH"] = str(RESULT)
    command = [_webots_executable(webots_executable)]
    if viewer:
        environment.setdefault("X30_WEBOTS_VIEWER_HOLD_SECONDS", "300")
        command.extend(["--mode=realtime", "--stdout", "--stderr"])
    else:
        command.extend(["--batch", "--mode=fast", "--no-rendering", "--stdout", "--stderr"])
    command.append(str(scene))
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
