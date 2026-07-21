import numpy as np
import os
import subprocess
import json
import time
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


class Sim2SimValidator:
    """True cross-simulator Sim-to-Sim validator.

    Run 1: MuJoCo with randomised physics (friction + mass perturbation)
    Run 2: Webots physics engine (real Webots subprocess execution)
    Run 3: MuJoCo with alternate target object (duck)
    """

    WEBOTS_EXE = r"C:\Users\Kauker\AppData\Local\Programs\Webots\msys64\mingw64\bin\webots.exe"
    WEBOTS_WORLD = os.path.normpath(os.path.join(
        _HERE, "webots_project", "worlds", "reachy_mini_tabletop.wbt"
    ))
    WEBOTS_RESULT = os.path.normpath(os.path.join(
        _HERE, "webots_sim2sim_result.json"
    ))

    def __init__(self, env_cls, policy_cls, metrics_cls):
        self.env_cls     = env_cls
        self.policy_cls  = policy_cls
        self.metrics_cls = metrics_cls

    def _run_mujoco(self, target_object: str = "apple", perturb: bool = False) -> dict:
        """Run one validation episode in MuJoCo."""
        env     = self.env_cls()
        policy  = self.policy_cls()
        metrics = self.metrics_cls()

        if perturb:
            friction_scale = float(np.random.uniform(0.75, 1.25))
            mass_scale     = float(np.random.uniform(0.80, 1.20))
            env.model.geom_friction[:, 0] *= friction_scale
            import mujoco as _mujoco
            apple_id = _mujoco.mj_name2id(env.model, _mujoco.mjtObj.mjOBJ_BODY, "apple")
            if apple_id >= 0:
                env.model.body_mass[apple_id] *= mass_scale
        else:
            friction_scale = 1.000
            mass_scale     = 1.000

        obs = env.reset(target_object=target_object)
        policy.reset()
        metrics.reset(obs)
        last_summary = {}

        while obs["sim_time"] < 8.0:
            action, _ = policy.compute_action(obs, last_summary)
            env.set_control(action)
            obs          = env.step(steps=5)
            last_summary = metrics.update(obs)
            if last_summary.get("task_completed", False):
                break

        s = metrics.get_summary()
        return {
            "simulator_engine":       "MuJoCo",
            "target_object":          target_object,
            "friction_scale":         round(friction_scale, 3),
            "mass_scale":             round(mass_scale, 3),
            "sim_duration_seconds":   round(float(obs["sim_time"]), 2),
            "task_completed":         s["task_completed"],
            "tracking_success_rate":  round(float(s["tracking_success_rate"]), 3),
            "success_rate_score":     round(float(s["success_rate_score"]), 3),
        }

    def _run_webots(self, target_object: str = "apple") -> dict:
        """Run one validation episode inside the real Webots physics engine."""
        # Remove stale result file
        if os.path.exists(self.WEBOTS_RESULT):
            os.remove(self.WEBOTS_RESULT)

        env = {
            **os.environ,
            "WEBOTS_HOME":   r"C:\Users\Kauker\AppData\Local\Programs\Webots",
            "REACHY_TARGET": target_object,
        }

        # Launch Webots headless with fast physics (no rendering)
        cmd = [
            self.WEBOTS_EXE,
            "--mode=fast",
            "--no-rendering",
            "--minimize",
            "--batch",
            self.WEBOTS_WORLD,
        ]

        proc = subprocess.Popen(
            cmd, env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait up to 120s for Webots to finish and write the result file
        deadline = time.time() + 120
        result = None
        while time.time() < deadline:
            time.sleep(2)
            if os.path.exists(self.WEBOTS_RESULT):
                try:
                    with open(self.WEBOTS_RESULT) as f:
                        result = json.load(f)
                    break
                except Exception:
                    pass
            if proc.poll() is not None:
                break

        proc.terminate()
        proc.wait()

        if result is None:
            # Webots didn't produce output — return partial result
            return {
                "simulator_engine":      "Webots",
                "target_object":         target_object,
                "friction_scale":        1.000,
                "mass_scale":            1.000,
                "sim_duration_seconds":  0.0,
                "task_completed":        False,
                "tracking_success_rate": 0.0,
                "success_rate_score":    0.0,
                "note":                  "Webots did not produce output within timeout",
            }

        result["friction_scale"] = 1.000
        result["mass_scale"]     = 1.000
        return result

    def run_validation(self, num_runs: int = 3) -> dict:
        results = []

        # Run 1: MuJoCo with physics perturbation
        r1 = self._run_mujoco(target_object="apple", perturb=True)
        r1["run_id"] = "sim2sim_run_1_mujoco_friction"
        results.append(r1)

        # Run 2: Real Webots physics engine (cross-simulator)
        r2 = self._run_webots(target_object="apple")
        r2["run_id"] = "sim2sim_run_2_webots_cross_engine"
        results.append(r2)

        # Run 3: MuJoCo with alternate target (duck)
        r3 = self._run_mujoco(target_object="duck", perturb=False)
        r3["run_id"] = "sim2sim_run_3_mujoco_duck_target"
        results.append(r3)

        avg_score = float(np.mean([r["success_rate_score"] for r in results]))

        return {
            "num_variations_tested":            len(results),
            "simulators_evaluated":             ["MuJoCo", "Webots"],
            "overall_sim2sim_robustness_score": round(avg_score, 3),
            "variation_details":                results,
        }
