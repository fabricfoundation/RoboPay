"""Sim-to-Sim validator: MuJoCo physics variations + real Webots subprocess.

IMPORTANT: This validator requires a real Webots installation for the cross-engine
validation. If Webots is not available, the validation FAILS — it does NOT
silently substitute a Python approximation.
"""

import json
import os
import subprocess
import sys
import time

import mujoco
import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_webots() -> str | None:
    """Locate the Webots executable on any platform without hardcoding paths."""
    import shutil
    import platform

    # 1. Environment variable override
    env_override = os.environ.get("WEBOTS_EXE")
    if env_override and os.path.isfile(env_override):
        return env_override

    # 2. Native Linux install locations
    if platform.system() == "Linux":
        for native_path in ("/opt/webots/webots", "/usr/local/bin/webots", "/usr/bin/webots"):
            if os.path.isfile(native_path):
                return native_path

    # 3. Look on $PATH
    for name in ("webots", "webots.exe"):
        found = shutil.which(name)
        if found:
            return found

    # 4. Fallback search
    candidates = []
    if platform.system() == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe",
        ]
        if local_app_data:
            candidates.append(
                os.path.join(local_app_data, "Programs", "Webots", "msys64", "mingw64", "bin", "webots.exe")
            )
    elif platform.system() == "Darwin":
        candidates = ["/Applications/Webots.app/Contents/MacOS/webots"]

    for path in candidates:
        if os.path.isfile(path):
            return path

    return None


_WEBOTS_WBT = os.path.normpath(os.path.join(_HERE, "scenes", "reachy_mini_simple.wbt"))
_WEBOTS_JSON = os.path.normpath(os.path.join(_HERE, "webots_sim2sim_result.json"))
_WEBOTS_TIMEOUT = 120  # seconds


class Sim2SimValidator:
    """Multi-simulator Sim-to-Sim validator.

    Run 1: MuJoCo with randomized friction + mass perturbation (apple target).
    Run 2: MuJoCo with randomized friction + mass (croissant target).
    Run 3: Real Webots subprocess — same policy, same scene, 3 targets.
    Run 4: MuJoCo with alternate target (duck) to verify multi-object tracking.

    If Webots is not available, run 3 FAILS with a clear error.
    There is NO silent Python fallback.
    """

    def __init__(self, env_cls, policy_cls, metrics_cls):
        self.env_cls = env_cls
        self.policy_cls = policy_cls
        self.metrics_cls = metrics_cls

    # ── private helpers ────────────────────────────────────────────────────────

    def _run_mujoco(self, target_object: str, friction_scale: float, mass_scale: float, run_id: str) -> dict:
        env = self.env_cls()
        policy = self.policy_cls()
        metrics = self.metrics_cls()

        env.model.geom_friction[:, 0] *= friction_scale
        body_id = mujoco.mj_name2id(env.model, mujoco.mjtObj.mjOBJ_BODY, target_object)
        if body_id >= 0:
            env.model.body_mass[body_id] *= mass_scale

        obs = env.reset(target_object=target_object)
        policy.reset()
        metrics.reset(obs)
        last = {}

        while obs["sim_time"] < 8.0:
            action, _ = policy.compute_action(obs, last)
            env.set_control(action)
            obs = env.step(steps=5)
            last = metrics.update(obs)
            if last.get("task_completed", False):
                break

        s = metrics.get_summary()
        return {
            "run_id": run_id,
            "simulator_engine": "MuJoCo",
            "target_object": target_object,
            "friction_scale": round(friction_scale, 3),
            "mass_scale": round(mass_scale, 3),
            "sim_duration_seconds": round(float(obs["sim_time"]), 2),
            "task_completed": s["task_completed"],
            "tracking_success_rate": round(float(s["tracking_success_rate"]), 3),
            "min_tracking_error_rad": round(float(s["min_tracking_error_rad"]), 3),
            "success_rate_score": round(float(s["success_rate_score"]), 3),
        }

    def _run_webots_subprocess(self) -> dict:
        """Launch real Webots subprocess in batch mode.

        FAILS if Webots is not installed. No fallback.
        """
        webots_exe = _find_webots()
        if not webots_exe or not os.path.isfile(webots_exe):
            return {
                "run_id": "sim2sim_run_3_webots",
                "simulator_engine": "Webots",
                "task_completed": False,
                "success_rate_score": 0.0,
                "error": (
                    "Webots executable not found. Install Webots R2023b+ and either "
                    "add it to PATH or set WEBOTS_EXE=/path/to/webots. "
                    "Sim-to-Sim validation REQUIRES a real second physics engine."
                ),
            }

        # Remove stale result file
        if os.path.exists(_WEBOTS_JSON):
            os.remove(_WEBOTS_JSON)

        cmd = [
            webots_exe,
            "--batch",
            "--mode=fast",
            "--no-rendering",
            _WEBOTS_WBT,
        ]

        print(f"[Sim2Sim] Launching Webots: {webots_exe}")
        print(f"[Sim2Sim] Scene: {_WEBOTS_WBT}")

        webots_env = dict(os.environ)
        # Tell the controller to auto-quit Webots when the episodes finish
        # (batch/sim2sim mode). Without this flag the controller keeps the
        # window open for interactive GUI inspection.
        webots_env["REACHY_SIM2SIM_BATCH"] = "1"
        # The "offscreen" Qt platform only exists on Linux. On Windows/Webots
        # bundles only the "windows" plugin, so setting offscreen crashes Webots
        # with "no Qt platform plugin could be initialized". --batch --no-rendering
        # is sufficient for headless operation on Windows.
        if sys.platform.startswith("linux"):
            webots_env["QT_QPA_PLATFORM"] = "offscreen"
        else:
            webots_env.pop("QT_QPA_PLATFORM", None)

        # Set PYTHONPATH so the controller can import the policy module
        webots_lib_python = os.path.join(os.path.dirname(webots_exe), "..", "lib", "controller", "python")
        bridge_root = os.path.dirname(_HERE)
        webots_env["PYTHONPATH"] = os.pathsep.join([
            bridge_root,
            _HERE,
            os.path.join(_HERE, "scenes", "controllers", "reachy_mini_controller"),
            os.path.normpath(webots_lib_python),
            webots_env.get("PYTHONPATH", ""),
        ])
        # Tell Webots where to find the controller directory
        webots_env["WEBOTS_CONTROLLER_PATH"] = os.path.join(_HERE, "scenes", "controllers")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=_HERE,
                env=webots_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as exc:
            return {
                "run_id": "sim2sim_run_3_webots",
                "simulator_engine": "Webots",
                "task_completed": False,
                "success_rate_score": 0.0,
                "error": f"Failed to start Webots process: {exc}",
            }

        # Poll until result JSON appears or timeout
        t0 = time.time()
        while time.time() - t0 < _WEBOTS_TIMEOUT:
            if os.path.exists(_WEBOTS_JSON):
                time.sleep(1.0)  # let the controller finish writing
                break
            if proc.poll() is not None:
                break
            time.sleep(1.0)

        # Terminate Webots
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        if not os.path.exists(_WEBOTS_JSON):
            out, err = proc.communicate(timeout=5) if proc.poll() is None else (b"", b"")
            return {
                "run_id": "sim2sim_run_3_webots",
                "simulator_engine": "Webots",
                "task_completed": False,
                "success_rate_score": 0.0,
                "error": (
                    f"Webots did not produce result JSON within {_WEBOTS_TIMEOUT}s. "
                    f"returncode={proc.returncode}. "
                    f"stdout={out.decode('utf-8', errors='ignore')[:500]}. "
                    f"stderr={err.decode('utf-8', errors='ignore')[:500]}"
                ),
            }

        try:
            with open(_WEBOTS_JSON) as f:
                raw = json.load(f)
            print(f"[Sim2Sim] Webots result: score={raw.get('success_rate_score', '?')}")
            raw["run_id"] = "sim2sim_run_3_webots_subprocess"
            raw["simulator_engine"] = "Webots"
            return raw
        except Exception as exc:
            return {
                "run_id": "sim2sim_run_3_webots",
                "simulator_engine": "Webots",
                "task_completed": False,
                "success_rate_score": 0.0,
                "error": f"Failed to parse Webots result JSON: {exc}",
            }

    # ── public ─────────────────────────────────────────────────────────────────

    def run_validation(self, num_runs: int = 4) -> dict:
        results = []
        simulators_actually_used = set()

        # Run 1 — MuJoCo, friction + mass noise, apple
        friction_scale = float(np.random.uniform(0.75, 1.25))
        mass_scale = float(np.random.uniform(0.80, 1.20))
        r1 = self._run_mujoco(
            target_object="apple",
            friction_scale=friction_scale,
            mass_scale=mass_scale,
            run_id="sim2sim_run_1_mujoco_apple",
        )
        results.append(r1)
        if r1["task_completed"]:
            simulators_actually_used.add("MuJoCo")

        # Run 2 — MuJoCo, friction + mass noise, croissant
        friction_scale2 = float(np.random.uniform(0.75, 1.25))
        mass_scale2 = float(np.random.uniform(0.80, 1.20))
        r2 = self._run_mujoco(
            target_object="croissant",
            friction_scale=friction_scale2,
            mass_scale=mass_scale2,
            run_id="sim2sim_run_2_mujoco_croissant",
        )
        results.append(r2)
        if r2["task_completed"]:
            simulators_actually_used.add("MuJoCo")

        # Run 3 — Real Webots subprocess (NO FALLBACK)
        r3 = self._run_webots_subprocess()
        results.append(r3)
        if r3.get("task_completed", False):
            simulators_actually_used.add("Webots")
        elif "error" in r3:
            print(f"[Sim2Sim] WARNING: Webots validation FAILED: {r3['error']}")

        # Run 4 — MuJoCo, duck target, nominal physics
        r4 = self._run_mujoco(
            target_object="duck",
            friction_scale=1.0,
            mass_scale=1.0,
            run_id="sim2sim_run_4_mujoco_duck",
        )
        results.append(r4)
        if r4["task_completed"]:
            simulators_actually_used.add("MuJoCo")

        # Compute score — Webots failure counts as 0
        scores = [r.get("success_rate_score", 0.0) for r in results]
        avg_score = float(np.mean(scores))

        # Report ONLY simulators that actually ran successfully
        simulators_list = sorted(simulators_actually_used)
        webots_ok = "Webots" in simulators_actually_used

        return {
            "num_variations_tested": len(results),
            "simulators_evaluated": simulators_list,
            "webots_validation_passed": webots_ok,
            "overall_sim2sim_robustness_score": round(avg_score, 3),
            "variation_details": results,
            "note": (
                "Sim-to-Sim requires BOTH MuJoCo and Webots to pass. "
                "If webots_validation_passed is false, the sim-to-sim requirement is NOT met."
                if not webots_ok
                else "Both physics engines validated the same policy independently."
            ),
        }
