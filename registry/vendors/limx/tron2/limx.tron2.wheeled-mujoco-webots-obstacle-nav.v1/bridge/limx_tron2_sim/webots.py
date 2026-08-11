"""Execute the same canonical course in a real Webots R2025a process."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .contracts import NavigationRequest
from .course import OBSTACLES, WAYPOINTS, webots_course_vrml
from .model import PROFILE_ROOT


WEBOTS_ROOT = PROFILE_ROOT / "simulators" / "webots"
SCENE_TEMPLATE = WEBOTS_ROOT / "scenes" / "tron2_obstacle_course_template.wbt"
GENERATED_SCENE = WEBOTS_ROOT / "scenes" / "tron2_obstacle_course.generated.wbt"
CONFIG_PATH = PROFILE_ROOT / "artifacts" / "generated" / "tron2_webots_config.json"
RESULT_PATH = PROFILE_ROOT / "artifacts" / "generated" / "tron2_webots_result.json"


def _webots_executable(configured: str | None = None) -> str:
    executable = configured or os.environ.get("WEBOTS_EXE") or shutil.which("webots")
    if not executable:
        raise FileNotFoundError("Webots R2025a is required; set WEBOTS_EXE when it is not on PATH")
    return executable


def _webots_home(executable: Path) -> Path:
    for candidate in (executable.parent, *executable.parents):
        if (candidate / "resources").is_dir():
            return candidate
    return executable.parent


def render_scene() -> Path:
    begin = "# BEGIN PROFILE_OBSTACLE_COURSE"
    end = "# END PROFILE_OBSTACLE_COURSE"
    template = SCENE_TEMPLATE.read_text(encoding="utf-8")
    if template.count(begin) != 1 or template.count(end) != 1:
        raise RuntimeError("TRON 2 Webots course markers are missing or ambiguous")
    prefix, remaining = template.split(begin, 1)
    _, suffix = remaining.split(end, 1)
    GENERATED_SCENE.write_text(f"{prefix}{begin}\n{webots_course_vrml()}\n{end}{suffix}", encoding="utf-8")
    return GENERATED_SCENE


def run_webots_episode(
    request: NavigationRequest,
    *,
    webots_executable: str | None = None,
    viewer: bool = False,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    from generate_webots_proto import generate

    generate()
    scene = render_scene()
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(
            {
                "skill_id": request.skill_id,
                "max_duration_sec": request.max_duration_sec,
                "obstacles": [obstacle.__dict__ for obstacle in OBSTACLES],
                "waypoints": WAYPOINTS,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    RESULT_PATH.unlink(missing_ok=True)
    environment = os.environ.copy()
    bridge_path = str(PROFILE_ROOT / "bridge")
    environment["PYTHONPATH"] = bridge_path + os.pathsep + environment.get("PYTHONPATH", "")
    environment["WEBOTS_CONTROLLER_PATH"] = str(WEBOTS_ROOT / "controllers")
    environment["LIMX_TRON2_WEBOTS_CONFIG_PATH"] = str(CONFIG_PATH)
    environment["LIMX_TRON2_WEBOTS_RESULT_PATH"] = str(RESULT_PATH)
    executable = Path(_webots_executable(webots_executable))
    environment.setdefault("WEBOTS_HOME", str(_webots_home(executable)))
    command = [str(executable)]
    if viewer:
        environment["LIMX_TRON2_WEBOTS_VIEWER_HOLD_SECONDS"] = "300"
        command.extend(["--mode=realtime", "--stdout", "--stderr"])
    else:
        environment.pop("LIMX_TRON2_WEBOTS_VIEWER_HOLD_SECONDS", None)
        command.extend(["--batch", "--mode=fast", "--no-rendering", "--stdout", "--stderr"])
    command.append(str(scene))
    log = None if viewer else tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=scene.parent,
            env=environment,
            stdout=None if viewer else log,
            stderr=None if viewer else log,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" and not viewer else 0,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and not RESULT_PATH.is_file():
            if process.poll() is not None:
                break
            time.sleep(0.25)
        if not RESULT_PATH.is_file():
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            diagnostic = ""
            if log is not None:
                log.seek(0)
                diagnostic = log.read()[-10000:]
            raise RuntimeError(
                "Webots did not produce its measured TRON 2 result JSON. "
                f"returncode={process.returncode}; log={diagnostic}"
            )
        result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
        if not viewer:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
        result["webots_return_code"] = process.poll()
        if not viewer and process.returncode not in {None, 0}:
            result["success"] = False
            result.setdefault("terminal_reason", "webots_process_failure")
        return result
    finally:
        if log is not None:
            log.close()
