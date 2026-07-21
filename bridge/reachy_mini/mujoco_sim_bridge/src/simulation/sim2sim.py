import numpy as np
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from webots_env import ReachyMiniWebotsEnvironment


class Sim2SimValidator:
    """Multi-simulator & physical variation Sim-to-Sim validator.

    Validates policy generalization across both MuJoCo physics perturbations
    and cross-simulator Webots execution.
    """

    def __init__(self, env_cls, policy_cls, metrics_cls):
        self.env_cls     = env_cls
        self.policy_cls  = policy_cls
        self.metrics_cls = metrics_cls

    def run_validation(self, num_runs: int = 3) -> dict:
        results = []

        # Run 1: MuJoCo Physics (Friction scale + mass perturbation)
        env1     = self.env_cls()
        policy1  = self.policy_cls()
        metrics1 = self.metrics_cls()

        friction_scale = float(np.random.uniform(0.75, 1.25))
        mass_scale     = float(np.random.uniform(0.80, 1.20))
        env1.model.geom_friction[:, 0] *= friction_scale

        import mujoco
        apple_id = mujoco.mj_name2id(env1.model, mujoco.mjtObj.mjOBJ_BODY, "apple")
        if apple_id >= 0:
            env1.model.body_mass[apple_id] *= mass_scale

        obs1 = env1.reset(target_object="apple")
        policy1.reset()
        metrics1.reset(obs1)

        last_summary1 = {}
        while obs1["sim_time"] < 8.0:
            action, _ = policy1.compute_action(obs1, last_summary1)
            env1.set_control(action)
            obs1          = env1.step(steps=5)
            last_summary1 = metrics1.update(obs1)
            if last_summary1.get("task_completed", False):
                break

        summary1 = metrics1.get_summary()
        results.append({
            "run_id":                "sim2sim_run_1_mujoco_friction",
            "simulator_engine":      "MuJoCo",
            "target_object":         "apple",
            "friction_scale":        round(friction_scale, 3),
            "mass_scale":            round(mass_scale, 3),
            "sim_duration_seconds":  round(float(obs1["sim_time"]), 2),
            "task_completed":        summary1["task_completed"],
            "tracking_success_rate": round(float(summary1["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(summary1["success_rate_score"]), 3),
        })

        # Run 2: Webots Cross-Simulator Engine Validation
        env2     = ReachyMiniWebotsEnvironment(target_object="apple")
        policy2  = self.policy_cls()
        metrics2 = self.metrics_cls()

        obs2 = env2.reset(target_object="apple")
        policy2.reset()
        metrics2.reset(obs2)

        last_summary2 = {}
        while obs2["sim_time"] < 8.0:
            action, _ = policy2.compute_action(obs2, last_summary2)
            env2.set_control(action)
            obs2          = env2.step(steps=5)
            last_summary2 = metrics2.update(obs2)
            if last_summary2.get("task_completed", False):
                break

        summary2 = metrics2.get_summary()
        results.append({
            "run_id":                "sim2sim_run_2_webots_cross_engine",
            "simulator_engine":      "Webots",
            "target_object":         "apple",
            "friction_scale":        1.000,
            "mass_scale":            1.000,
            "sim_duration_seconds":  round(float(obs2["sim_time"]), 2),
            "task_completed":        summary2["task_completed"],
            "tracking_success_rate": round(float(summary2["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(summary2["success_rate_score"]), 3),
        })

        # Run 3: MuJoCo Multi-Target Verification (Duck target)
        env3     = self.env_cls()
        policy3  = self.policy_cls()
        metrics3 = self.metrics_cls()

        obs3 = env3.reset(target_object="duck")
        policy3.reset()
        metrics3.reset(obs3)

        last_summary3 = {}
        while obs3["sim_time"] < 8.0:
            action, _ = policy3.compute_action(obs3, last_summary3)
            env3.set_control(action)
            obs3          = env3.step(steps=5)
            last_summary3 = metrics3.update(obs3)
            if last_summary3.get("task_completed", False):
                break

        summary3 = metrics3.get_summary()
        results.append({
            "run_id":                "sim2sim_run_3_mujoco_duck_target",
            "simulator_engine":      "MuJoCo",
            "target_object":         "duck",
            "friction_scale":        1.000,
            "mass_scale":            1.000,
            "sim_duration_seconds":  round(float(obs3["sim_time"]), 2),
            "task_completed":        summary3["task_completed"],
            "tracking_success_rate": round(float(summary3["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(summary3["success_rate_score"]), 3),
        })

        avg_score = float(np.mean([r["success_rate_score"] for r in results]))
        return {
            "num_variations_tested":            len(results),
            "simulators_evaluated":             ["MuJoCo", "Webots"],
            "overall_sim2sim_robustness_score": round(avg_score, 3),
            "variation_details":                results,
        }
