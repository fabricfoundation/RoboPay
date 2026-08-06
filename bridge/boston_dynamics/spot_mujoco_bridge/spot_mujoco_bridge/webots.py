"""Launch the actual Webots R2025a obstacle-course validation."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCENE = PACKAGE_ROOT / "scenes" / "spot_obstacle_course.wbt"
RESULT = PACKAGE_ROOT / "scenes" / "webots_obstacle_course_result.json"


def _webots_home(executable: Path) -> Path:
    """Find the installation root for both Linux and Windows Webots layouts."""

    for candidate in (executable.parent, *executable.parents):
        if (candidate / "resources").is_dir():
            return candidate
    return executable.parents[min(3, len(executable.parents) - 1)]


def find_webots() -> Path | None:
    """Find Webots from an override, PATH, or standard Windows install paths."""

    override = os.environ.get("WEBOTS_EXE")
    if override and Path(override).is_file():
        return Path(override)
    for executable in ("webots", "webots.exe"):
        found = shutil.which(executable)
        if found:
            return Path(found)
    candidates = [
        Path(r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Webots" / "msys64" / "mingw64" / "bin" / "webots.exe",
    ]
    return next((item for item in candidates if item.is_file()), None)


def run_webots_validation(timeout_seconds: int = 150) -> dict:
    """Run Webots headlessly and return the controller's unmodified result."""

    executable = find_webots()
    if executable is None:
        return {
            "simulator_engine": "Webots",
            "status": "failure",
            "success": False,
            "error": "Webots R2025a executable was not found. Set WEBOTS_EXE.",
        }
    RESULT.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["WEBOTS_CONTROLLER_PATH"] = str(PACKAGE_ROOT / "controllers")
    environment.pop("SPOT_WEBOTS_HOLD_VIEWER", None)
    # On Windows webots.exe is a launcher. WEBOTS_HOME lets it start the
    # persistent webots-bin child; without it the launcher may return before a
    # world or its controller has been initialized.
    environment.setdefault("WEBOTS_HOME", str(_webots_home(executable)))
    command = [str(executable), "--batch", "--mode=fast", "--no-rendering", "--stdout", "--stderr", str(SCENE)]
    process = subprocess.Popen(
        command,
        cwd=SCENE.parent,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if RESULT.is_file():
            # The Supervisor calls simulationQuit after writing this file. The
            # launcher can take a moment to reap its GUI child on Windows.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
            result = json.loads(RESULT.read_text(encoding="utf-8"))
            result["webots_return_code"] = process.poll()
            return result
        time.sleep(0.25)

    process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
    return {
        "simulator_engine": "Webots",
        "status": "failure",
        "success": False,
        "error": f"Webots did not produce a result within {timeout_seconds} seconds.",
        "webots_return_code": process.returncode,
        "stdout": stdout[-1500:],
        "stderr": stderr[-1500:],
    }


def launch_webots_viewer() -> int:
    """Open the Webots GUI in real-time mode and return its process ID."""

    executable = find_webots()
    if executable is None:
        raise FileNotFoundError("Webots R2025a executable was not found. Set WEBOTS_EXE.")
    environment = dict(os.environ)
    environment["WEBOTS_CONTROLLER_PATH"] = str(PACKAGE_ROOT / "controllers")
    environment.setdefault("WEBOTS_HOME", str(_webots_home(executable)))
    # The controller pauses on its final state in visual mode, so the user can
    # inspect the Spot and obstacle layout before closing Webots.
    environment["SPOT_WEBOTS_HOLD_VIEWER"] = "1"
    process = subprocess.Popen(
        [str(executable), "--mode=realtime", str(SCENE)],
        cwd=SCENE.parent,
        env=environment,
        creationflags=0,
    )
    return process.pid


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Spot's Webots cross-engine validation.")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--viewer", action="store_true", help="Open the real-time Webots GUI instead of headless validation.")
    args = parser.parse_args()
    if args.viewer:
        pid = launch_webots_viewer()
        print(f"Webots viewer started (PID {pid}). Close Webots when you are done inspecting it.")
        return
    result = run_webots_validation(args.timeout)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    raise SystemExit(0 if result.get("success") else 1)
