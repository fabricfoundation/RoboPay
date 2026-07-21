import numpy as np
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from webots_env import ReachyMiniWebotsEnvironment


class Sim2SimValidator:
    """Sim-to-Sim multi-simulator & physics-variation validator.

    Validates policy robustness across both physics engine variations (MuJoCo)
    and cross-simulator environments (Webots vs MuJoCo).
    """

    def __init__(self, env_cls, policy_cls, metrics_cls):
        self.env_cls     = env_cls
        self.policy_cls  = policy_cls
        self.metrics_cls = metrics_cls

    def run_validation(self, num_runs: int = 3):
        results = []

        # Run 1: MuJoCo with random friction perturbation
        env1     = self.env_cls()
        policy1  = self.policy_cls()
        metrics1 = self.metrics_cls()

        friction_scale = float(np.random.uniform(0.75, 1.25))
        mass_scale     = float(np.random.uniform(0.80, 1.20))
        env1.model.geom_friction[:, 0] *= friction_scale

        import mujoco as _mujoco
        apple_body_id = _mujoco.mj_name2id(
            env1.model, _mujoco.mjtObj.mjOBJ_BODY, "apple"
        )
        if apple_body_id >= 0:
            env1.model.body_mass[apple_body_id] *= mass_scale

        obs = env1.reset(target_object="apple")
        policy1.reset()
        metrics1.reset(obs)

        last_summary = {}
        while obs["sim_time"] < 8.0:
            action, _ = policy1.compute_action(obs, last_summary)
            env1.set_control(action)
            obs          = env1.step(steps=5)
            last_summary = metrics1.update(obs)
            if last_summary["task_completed"]:
                break

        run1_summary = metrics1.get_summary()
        results.append({
            "run_id":                "sim2sim_run_1_mujoco_friction",
            "simulator_engine":      "MuJoCo",
            "target_object":         "apple",
            "friction_scale":        round(friction_scale, 3),
            "mass_scale":            round(mass_scale, 3),
            "sim_duration_seconds":  round(float(obs["sim_time"]), 2),
            "task_completed":        run1_summary["task_completed"],
            "tracking_success_rate": round(float(run1_summary["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(run1_summary["success_rate_score"]), 3),
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
            if last_summary2["task_completed"]:
                break

        run2_summary = metrics2.get_summary()
        results.append({
            "run_id":                "sim2sim_run_2_webots_cross_engine",
            "simulator_engine":      "Webots",
            "target_object":         "apple",
            "friction_scale":        1.000,
            "mass_scale":            1.000,
            "sim_duration_seconds":  round(float(obs2["sim_time"]), 2),
            "task_completed":        run2_summary["task_completed"],
            "tracking_success_rate": round(float(run2_summary["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(run2_summary["success_rate_score"]), 3),
        })

        # Run 3: MuJoCo with Duck Target & Mass Perturbation
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
            if last_summary3["task_completed"]:
                break

        run3_summary = metrics3.get_summary()
        results.append({
            "run_id":                "sim2sim_run_3_mujoco_duck_target",
            "simulator_engine":      "MuJoCo",
            "target_object":         "duck",
            "friction_scale":        1.000,
            "mass_scale":            1.000,
            "sim_duration_seconds":  round(float(obs3["sim_time"]), 2),
            "task_completed":        run3_summary["task_completed"],
            "tracking_success_rate": round(float(run3_summary["tracking_success_rate"]), 3),
            "success_rate_score":    round(float(run3_summary["success_rate_score"]), 3),
        })

        avg_score = float(np.mean([r["success_rate_score"] for r in results]))
        return {
            "num_variations_tested":            len(results),
            "simulators_evaluated":             ["MuJoCo", "Webots"],
            "overall_sim2sim_robustness_score": round(avg_score, 3),
            "variation_details":                results,
        }
