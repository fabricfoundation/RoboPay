"""Sim-to-Sim verification: MuJoCo (apple, croissant, duck) + Webots subprocess."""
import sys
import json
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from simulation.environment import ReachyMiniEnvironment
from simulation.metrics import SimulationMetricsTracker
from simulation.sim2sim import Sim2SimValidator
from policy.controller import ReachyTaskPolicy

print("Running Sim-to-Sim validation (4 runs: MuJoCo x3 + Webots subprocess x1)...")
print()

validator = Sim2SimValidator(ReachyMiniEnvironment, ReachyTaskPolicy, SimulationMetricsTracker)
result    = validator.run_validation()

print(f"{'run_id':<38} {'engine':<28} {'target':<12} {'score':<8} {'done'}")
print("-" * 100)
for r in result["variation_details"]:
    engine  = r.get("simulator_engine", "?")
    score   = r.get("success_rate_score", "?")
    done    = r.get("task_completed", "?")
    run_id  = r.get("run_id", "?")
    target  = r.get("target_object", r.get("targets_tracked", "?"))
    print(f"  {run_id:<36} {engine:<28} {str(target):<12} {str(score):<8} {done}")

print()
print(f"overall_sim2sim_robustness_score : {result['overall_sim2sim_robustness_score']}")
print(f"simulators_evaluated             : {result['simulators_evaluated']}")
print(f"num_variations_tested            : {result['num_variations_tested']}")
