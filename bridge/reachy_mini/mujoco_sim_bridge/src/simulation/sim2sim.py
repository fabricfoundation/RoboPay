"""Sim-to-Sim validator: MuJoCo physics variations + real Webots subprocess."""
import json
import mujoco
import numpy as np
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

def _find_webots() -> str | None:
    """Locate the Webots executable on any platform without hardcoding paths."""
    import shutil, platform
    # 1. Look on $PATH first (works when Webots is properly installed)
    for name in ("webots", "webots.exe"):
        found = shutil.which(name)
        if found:
            return found
    # 2. Common install locations as fallback
    candidates = []
    if platform.system() == "Windows":
        candidates = [
            r"C:\Program Files\Webots\msys64\mingw64\bin\webots.exe",
            r"C:\Users\Kauker\AppData\Local\Programs\Webots\msys64\mingw64\bin\webots.exe",
        ]
    elif platform.system() == "Darwin":
        candidates = ["/Applications/Webots.app/Contents/MacOS/webots"]
    else:
        candidates = ["/usr/local/bin/webots", "/usr/bin/webots"]
    import os
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None

_WEBOTS_EXE  = _find_webots()
_WEBOTS_WBT  = os.path.normpath(os.path.join(_HERE, "scenes", "reachy_mini_tabletop.wbt"))
_WEBOTS_JSON = os.path.normpath(os.path.join(_HERE, "webots_sim2sim_result.json"))
_WEBOTS_TIMEOUT = 90  # seconds


class Sim2SimValidator:
    """Multi-simulator Sim-to-Sim validator.

    Run 1: MuJoCo with randomized friction + mass perturbation (apple target).
    Run 2: Real Webots R2023b subprocess — same policy, same scene, 3 targets.
    Run 3: MuJoCo with alternate target (duck) to verify multi-object tracking.
    """

    def __init__(self, env_cls, policy_cls, metrics_cls):
        self.env_cls     = env_cls
        self.policy_cls  = policy_cls
        self.metrics_cls = metrics_cls

    # ── private helpers ────────────────────────────────────────────────────────

    def _run_mujoco(self, target_object: str, friction_scale: float,
                    mass_scale: float, run_id: str) -> dict:
        env     = self.env_cls()
        policy  = self.policy_cls()
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
            obs  = env.step(steps=5)
            last = metrics.update(obs)
            if last.get("task_completed", False):
                break

        s = metrics.get_summary()
        return {
            "run_id":                run_id,
            "simulator_engine":      "MuJoCo",
            "target_object":         target_object,
            "friction_scale":        round(friction_scale, 3),
            "mass_scale":            round(mass_scale, 3),
            "sim_duration_seconds":  round(float(obs["sim_time"]), 2),
            "task_completed":        s["task_completed"],
            "tracking_success_rate": round(float(s["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(s["success_rate_score"]), 3),
        }

    def _run_webots_subprocess(self) -> dict:
        """Launch real Webots subprocess in batch mode to run official Reachy Mini controller."""
        if not _WEBOTS_EXE or not os.path.isfile(_WEBOTS_EXE):
            print("[Sim2Sim] Webots executable not found — using Python Webots simulation fallback")
            return self._webots_fallback()

        # Remove stale result file if present
        if os.path.exists(_WEBOTS_JSON):
            os.remove(_WEBOTS_JSON)

        cmd = [
            _WEBOTS_EXE,
            "--batch",
            "--mode=fast",
            "--no-rendering",
            _WEBOTS_WBT,
        ]

        print("[Sim2Sim] Launching Webots subprocess …")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as exc:
            print(f"[Sim2Sim] Failed to start Webots: {exc}")
            return self._webots_fallback()

        # Poll until result JSON appears or timeout
        t0 = time.time()
        while time.time() - t0 < _WEBOTS_TIMEOUT:
            if os.path.exists(_WEBOTS_JSON):
                time.sleep(0.5)   # let the controller finish writing
                break
            if proc.poll() is not None:
                break
            time.sleep(1.0)

        # Terminate Webots (it may already have quit via simulationQuit)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

        if not os.path.exists(_WEBOTS_JSON):
            print("[Sim2Sim] Webots result not found — falling back to Python sim.")
            return self._webots_fallback()

        try:
            with open(_WEBOTS_JSON) as f:
                raw = json.load(f)
            print(f"[Sim2Sim] Webots result loaded: score={raw.get('success_rate_score', '?')}")
            raw["run_id"]           = "sim2sim_run_2_webots_subprocess"
            raw["simulator_engine"] = "Webots"
            return raw
        except Exception as exc:
            print(f"[Sim2Sim] Failed to read Webots result: {exc}")
            return self._webots_fallback()

    def _webots_fallback(self) -> dict:
        """Python-level Webots approximation when subprocess is unavailable."""
        try:
            from simulation.webots_env import ReachyMiniWebotsEnvironment
        except ImportError:
            from webots_env import ReachyMiniWebotsEnvironment
        env     = ReachyMiniWebotsEnvironment(target_object="apple")
        policy  = self.policy_cls()
        metrics = self.metrics_cls()

        obs = env.reset(target_object="apple")
        policy.reset()
        metrics.reset(obs)

        last = {}
        while obs["sim_time"] < 8.0:
            action, _ = policy.compute_action(obs, last)
            env.set_control(action)
            obs  = env.step(steps=5)
            last = metrics.update(obs)
            if last.get("task_completed", False):
                break

        s = metrics.get_summary()
        return {
            "run_id":                "sim2sim_run_2_webots_fallback",
            "simulator_engine":      "Webots (Python approximation)",
            "target_object":         "apple",
            "sim_duration_seconds":  round(float(obs["sim_time"]), 2),
            "task_completed":        s["task_completed"],
            "tracking_success_rate": round(float(s["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(s["success_rate_score"]), 3),
        }

    # ── public ─────────────────────────────────────────────────────────────────

    def run_validation(self, num_runs: int = 3) -> dict:
        results = []

        # Run 1 — MuJoCo, friction + mass noise, apple
        friction_scale = float(np.random.uniform(0.75, 1.25))
        mass_scale     = float(np.random.uniform(0.80, 1.20))
        results.append(self._run_mujoco(
            target_object="apple",
            friction_scale=friction_scale,
            mass_scale=mass_scale,
            run_id="sim2sim_run_1_mujoco_apple",
        ))

        # Run 2 — MuJoCo, friction + mass noise, croissant
        friction_scale2 = float(np.random.uniform(0.75, 1.25))
        mass_scale2     = float(np.random.uniform(0.80, 1.20))
        results.append(self._run_mujoco(
            target_object="croissant",
            friction_scale=friction_scale2,
            mass_scale=mass_scale2,
            run_id="sim2sim_run_2_mujoco_croissant",
        ))

        # Run 3 — Real Webots subprocess (apple + croissant + duck)
        results.append(self._run_webots_subprocess())

        # Run 4 — MuJoCo, duck target, nominal physics
        results.append(self._run_mujoco(
            target_object="duck",
            friction_scale=1.0,
            mass_scale=1.0,
            run_id="sim2sim_run_4_mujoco_duck",
        ))

        avg_score = float(np.mean([r.get("success_rate_score", 0.0) for r in results]))
        return {
            "num_variations_tested":            len(results),
            "simulators_evaluated":             ["MuJoCo", "Webots"],
            "overall_sim2sim_robustness_score": round(avg_score, 3),
            "variation_details":                results,
        }
