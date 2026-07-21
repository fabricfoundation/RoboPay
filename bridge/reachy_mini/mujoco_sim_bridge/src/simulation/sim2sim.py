import numpy as np


class Sim2SimValidator:
    """Sim-to-Sim validation runner for Reachy Mini head-tracking task.

    Tests the same task (apple tracking) under 3 randomised physics conditions
    (friction scale, object mass) to validate policy robustness across
    simulator variations.
    """

    def __init__(self, env_cls, policy_cls, metrics_cls):
        self.env_cls     = env_cls
        self.policy_cls  = policy_cls
        self.metrics_cls = metrics_cls

    def run_validation(self, num_runs: int = 3):
        results = []
        for i in range(num_runs):
            env     = self.env_cls()
            policy  = self.policy_cls()
            metrics = self.metrics_cls()

            # Randomise floor/table friction and the apple's mass
            friction_scale = float(np.random.uniform(0.75, 1.25))
            mass_scale     = float(np.random.uniform(0.80, 1.20))
            env.model.geom_friction[:, 0] *= friction_scale

            # Scale apple body mass
            import mujoco as _mujoco
            apple_body_id = _mujoco.mj_name2id(
                env.model, _mujoco.mjtObj.mjOBJ_BODY, "apple"
            )
            if apple_body_id >= 0:
                env.model.body_mass[apple_body_id] *= mass_scale

            obs = env.reset(target_object="apple")
            policy.reset()
            metrics.reset(obs)

            step_count   = 0
            last_summary = {}
            while obs["sim_time"] < 8.0:
                action, _ = policy.compute_action(obs, last_summary)
                env.set_control(action)
                obs          = env.step(steps=5)
                last_summary = metrics.update(obs)
                step_count  += 1
                if last_summary["task_completed"]:
                    break

            run_summary = metrics.get_summary()
            results.append({
                "run_id":                f"sim2sim_variation_{i + 1}",
                "target_object":         "apple",
                "friction_scale":        round(friction_scale, 3),
                "mass_scale":            round(mass_scale, 3),
                "sim_duration_seconds":  round(float(obs["sim_time"]), 2),
                "task_completed":        run_summary["task_completed"],
                "tracking_success_rate": round(float(run_summary["tracking_success_rate"]), 3),
                "success_rate_score":    round(float(run_summary["success_rate_score"]), 3),
            })

        avg_score = float(np.mean([r["success_rate_score"] for r in results]))
        return {
            "num_variations_tested":            num_runs,
            "overall_sim2sim_robustness_score": round(avg_score, 3),
            "variation_details":                results,
        }
