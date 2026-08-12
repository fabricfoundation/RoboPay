#!/usr/bin/env python3
"""Webots controller for the Go2 sim-to-sim harness.

This controller is attached to the DEF GO2 robot in go2_sim2sim.wbt. It runs
the MuJoCo-vs-Webots foot-position measurement
(``test_sim2sim_go2_webots``): the harness re-runs every paid skill in
MuJoCo, then drives the exact same joint targets into the Webots servo chain
through the Supervisor API and reads the foot-tip positions reported by the
Webots physics engine.

The report is written next to the harness:
``simulation/webots/go2_webots_sim2sim_report.json``
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
WEBOTS_DIR = HERE.parent
SIMULATION_DIR = WEBOTS_DIR.parent

for path in (str(SIMULATION_DIR / "go2"), str(WEBOTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import test_sim2sim_go2_webots as harness  # noqa: E402


def main():
    harness.main()


if __name__ == "__main__":
    main()
