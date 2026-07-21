"""Visualizer for Reachy Mini MuJoCo simulation.

Runs the simulation in continuous loop at 1.0x real-time speed with smooth,
physically realistic motor motion (slew-rate velocity limited).
Close the viewer window or press ESC to exit.
"""
import sys
import time
import pathlib

sys.path.insert(0, 'RoboPay/bridge/reachy_mini/mujoco_sim_bridge')
sys.path.insert(0, 'RoboPay/bridge/reachy_mini/mujoco_sim_bridge/src')

import mujoco
import mujoco.viewer
from simulation.environment import ReachyMiniEnvironment
from policy.controller import ReachyTaskPolicy
from simulation.metrics import SimulationMetricsTracker


def main():
    print("=" * 70)
    print("  Reachy Mini — Natural 1.0x Real-Time 3D Viewer")
    print("  Targets cycle: APPLE -> CROISSANT -> DUCK")
    print("  Close viewer window or press ESC to stop.")
    print("=" * 70)

    env     = ReachyMiniEnvironment()
    policy  = ReachyTaskPolicy()
    metrics = SimulationMetricsTracker()

    targets = ["apple", "croissant", "duck"]
    target_idx = 0

    with mujoco.viewer.launch_passive(env.model, env.data) as viewer:
        while viewer.is_running():
            current_target = targets[target_idx % len(targets)]
            print(f"\n=======================================================")
            print(f"  TARGET: {current_target.upper()}")
            print(f"=======================================================")

            obs = env.reset(target_object=current_target)
            policy.reset()
            metrics.reset(obs)
            last_phase = None

            while viewer.is_running() and obs["sim_time"] < 8.0:
                step_start = time.time()

                action, phase = policy.compute_action(obs)
                env.set_control(action)
                obs = env.step(steps=5)
                metrics.update(obs)

                if phase != last_phase:
                    print(f"  t={obs['sim_time']:.2f}s | PHASE: {phase}")
                    if phase == "EXPRESSIVE":
                        print("  >>> CELEBRATION DANCE STARTED! <<<")
                    last_phase = phase

                viewer.sync()

                # Natural 1.0x real-time speed synchronization
                dt = env.model.opt.timestep * 5
                elapsed = time.time() - step_start
                if dt > elapsed:
                    time.sleep(dt - elapsed)

            target_idx += 1
            time.sleep(0.8)

    print("\nViewer closed.")


if __name__ == "__main__":
    main()
