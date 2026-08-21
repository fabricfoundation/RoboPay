"""Run the Atlas DRC legacy wave against Webots' built-in Atlas PROTO."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCENE = PACKAGE_ROOT / "scenes" / "atlas_paid_wave.wbt"
RESULT = PACKAGE_ROOT / "scenes" / "webots_wave_result.json"


def find_webots() -> Path | None:
    override = os.environ.get("WEBOTS_EXE")
    if override and Path(override).is_file():
        return Path(override)
    for candidate in ("webots", "webots.exe"):
        found = shutil.which(candidate)
        if found:
            return Path(found)
    return None


def run_webots_validation(timeout_seconds: int = 60) -> dict:
    """Run R2025a headlessly and return the controller-written state metrics."""

    executable = find_webots()
    if executable is None:
        return {
            "simulator_engine": "Webots",
            "success": False,
            "status": "failure",
            "error": "Webots R2025a executable not found; set WEBOTS_EXE.",
        }
    RESULT.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment["WEBOTS_CONTROLLER_PATH"] = str(PACKAGE_ROOT / "controllers")
    capture_visual = bool(
        environment.get("ATLAS_WEBOTS_RECORDING_PATH", "").strip()
        or environment.get("ATLAS_WEBOTS_HOLD_SECONDS", "").strip()
    )
    if sys.platform != "win32" and not capture_visual:
        environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    elif sys.platform == "win32":
        # The Windows distribution ships only the native Qt platform plugin;
        # inheriting CI's offscreen setting prevents the GUI from starting.
        environment.pop("QT_QPA_PLATFORM", None)
    mode = "realtime" if capture_visual else "fast"
    command = [str(executable), f"--mode={mode}", "--stdout", "--stderr", str(SCENE)]
    if not capture_visual:
        command[1:1] = ["--batch", "--no-rendering"]
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
            try:
                hold_seconds = float(environment.get("ATLAS_WEBOTS_HOLD_SECONDS", "0") or 0)
                process.wait(timeout=max(10.0, hold_seconds + 5.0))
            except subprocess.TimeoutExpired:
                process.terminate()
            result = json.loads(RESULT.read_text(encoding="utf-8"))
            result["webots_return_code"] = process.poll()
            return result
        time.sleep(0.2)
    process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    return {
        "simulator_engine": "Webots",
        "success": False,
        "status": "failure",
        "error": f"Webots did not write a result within {timeout_seconds} seconds.",
        "stdout": stdout[-1500:],
        "stderr": stderr[-1500:],
    }
