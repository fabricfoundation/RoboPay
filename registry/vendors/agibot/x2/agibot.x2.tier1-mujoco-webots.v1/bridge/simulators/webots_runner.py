"""Webots episode runner for the obstacle-avoidance navigation skill."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agibot_x2_tier1_bridge import SimOutcome  # noqa: E402

WEBOTS_BIN = os.environ.get("WEBOTS_BIN", "webots")
LAUNCH_TIMEOUT_MARGIN_SEC = 30.0
RESULT_POLL_INTERVAL_SEC = 0.5


class WebotsRunner:
    def __init__(self, world_path: str) -> None:
        self.world_path = str(Path(world_path).resolve())
        self._policy_dir = str(Path(__file__).resolve().parents[1] / "policy")

    def run_episode(self, *, target_x: float, target_y: float, max_duration_sec: float) -> SimOutcome:
        # webots process exit is not a reliable completion signal (snap
        # launcher quirk); poll for the result file instead. Temp dir must
        # live in the project tree -- snap confinement blocks /tmp and $HOME.
        tmp_root = Path(__file__).resolve().parents[1] / ".webots_episode_tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix="episode_", dir=str(tmp_root))
        result_path = Path(tmp_dir) / "result.json"
        try:
            env = {
                **os.environ,
                "ROBOPAY_TARGET_X": str(target_x),
                "ROBOPAY_TARGET_Y": str(target_y),
                "ROBOPAY_MAX_DURATION_SEC": str(max_duration_sec),
                "ROBOPAY_RESULT_FILE": str(result_path),
                "ROBOPAY_POLICY_PATH": self._policy_dir,
            }
            cmd = [
                WEBOTS_BIN, "--batch", "--mode=fast", "--no-rendering",
                "--stdout", "--stderr", self.world_path,
            ]
            deadline = time.monotonic() + max_duration_sec + LAUNCH_TIMEOUT_MARGIN_SEC
            proc = subprocess.Popen(env=env, args=cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            while time.monotonic() < deadline:
                if result_path.exists():
                    try:
                        data = json.loads(result_path.read_text())
                    except (json.JSONDecodeError, OSError):
                        time.sleep(RESULT_POLL_INTERVAL_SEC)
                        continue
                    return SimOutcome(
                        reached_target=data["reached_target"],
                        collided=data["collided"],
                        timed_out=data["timed_out"],
                        simulator="webots",
                        detail=data["detail"],
                    )
                if proc.poll() is not None and not result_path.exists():
                    _, stderr = proc.communicate(timeout=5)
                    return SimOutcome(
                        reached_target=False, collided=False, timed_out=False,
                        simulator="webots",
                        detail=f"webots exited (code {proc.returncode}) with no result file; "
                               f"stderr tail: {stderr[-500:]}",
                    )
                time.sleep(RESULT_POLL_INTERVAL_SEC)

            proc.kill()
            return SimOutcome(
                reached_target=False, collided=False, timed_out=True,
                simulator="webots", detail="no result file within timeout",
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def close(self) -> None:
        return None
