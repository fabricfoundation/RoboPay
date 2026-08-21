"""Launch the native Webots validation for the supported G1 station."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCENE = PACKAGE_ROOT / "scenes" / "g1_inspection_station_ci.wbt"
VIEWER_SCENE = PACKAGE_ROOT / "scenes" / "g1_inspection_station.wbt"
RESULT = PACKAGE_ROOT / "scenes" / "webots_inspection_result.json"
PROTOS = (PACKAGE_ROOT / "scenes" / "G129dof.proto", PACKAGE_ROOT / "scenes" / "G129dofCI.proto")


def find_webots() -> Path | None:
    override = os.environ.get("WEBOTS_EXE")
    if override and Path(override).is_file():
        return Path(override)
    for name in ("webots", "webots.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    candidates = (
        Path(r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Webots" / "msys64" / "mingw64" / "bin" / "webots.exe",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _webots_home(executable: Path) -> Path:
    for candidate in (executable.parent, *executable.parents):
        if (candidate / "resources").is_dir():
            return candidate
    return executable.parent


def ensure_proto() -> None:
    if any(not proto.is_file() for proto in PROTOS):
        subprocess.run([sys.executable, str(PACKAGE_ROOT / "build_webots_model.py")], check=True)


def run_webots_validation(timeout_seconds: int = 300) -> dict:
    executable = find_webots()
    if executable is None:
        return {"simulator_engine": "Webots", "status": "failure", "success": False, "error": "Webots R2025a not found; set WEBOTS_EXE."}
    ensure_proto()
    RESULT.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["WEBOTS_CONTROLLER_PATH"] = str(PACKAGE_ROOT / "controllers")
    environment["PYTHONPATH"] = str(PACKAGE_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    environment.setdefault("WEBOTS_HOME", str(_webots_home(executable)))
    environment.pop("UNITREE_G1_WEBOTS_HOLD_VIEWER", None)
    process = subprocess.Popen(
        [str(executable), "--batch", "--mode=fast", "--stdout", "--stderr", str(SCENE)],
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
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
            result = json.loads(RESULT.read_text(encoding="utf-8"))
            result["webots_return_code"] = process.poll()
            return result
        if process.poll() is not None:
            break
        time.sleep(0.25)
    if process.poll() is None:
        process.terminate()
    stdout, stderr = process.communicate(timeout=10)
    return {
        "simulator_engine": "Webots", "status": "failure", "success": False,
        "error": f"Webots produced no result within {timeout_seconds} seconds.",
        "webots_return_code": process.returncode, "stdout": stdout[-2000:], "stderr": stderr[-2000:],
    }


def launch_webots_viewer() -> int:
    executable = find_webots()
    if executable is None:
        raise FileNotFoundError("Webots R2025a not found; set WEBOTS_EXE.")
    ensure_proto()
    environment = dict(os.environ)
    environment["WEBOTS_CONTROLLER_PATH"] = str(PACKAGE_ROOT / "controllers")
    environment["PYTHONPATH"] = str(PACKAGE_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    environment["UNITREE_G1_WEBOTS_HOLD_VIEWER"] = "1"
    environment.setdefault("UNITREE_G1_WEBOTS_START_HOLD_SECONDS", "4")
    environment.setdefault("UNITREE_G1_WEBOTS_TARGET_HOLD_SECONDS", "2")
    environment.setdefault("WEBOTS_HOME", str(_webots_home(executable)))
    process = subprocess.Popen([str(executable), "--mode=realtime", str(VIEWER_SCENE)], cwd=VIEWER_SCENE.parent, env=environment)
    return process.pid
