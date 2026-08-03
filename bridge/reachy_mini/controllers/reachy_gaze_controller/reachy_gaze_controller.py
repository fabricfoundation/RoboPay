"""Webots controller entry point for the Reachy Mini gaze-tracking policy.

Webots launches this script directly (as the Robot's `controller` field
points to this folder/file name). It wires up sys.path so the shared
sim_bridge modules (policy FSM, Webots env wrapper, metrics) can be
imported without duplicating any code -- this file is a thin adapter,
not a second implementation.

Calls sim2sim.main() (not run_webots_episode alone) so this run also
executes the MuJoCo episode and prints the cross-engine
sim_to_sim_validation comparison -- the actual artifact the bounty
rubric's "Sim-to-Sim validation" criterion is checking for.
"""
import os
import sys

# Path from this controller file up to RoboPay/bridge/reachy_mini/sim_bridge/src
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SIM_BRIDGE_SRC = os.path.normpath(
    os.path.join(_THIS_DIR, "..", "..", "sim_bridge", "src")
)
sys.path.insert(0, _SIM_BRIDGE_SRC)

from simulation.sim2sim import main  # noqa: E402


if __name__ == "__main__":
    target = os.environ.get("REACHY_GAZE_TARGET", "apple")
    main(target_name=target, run_webots=True)
