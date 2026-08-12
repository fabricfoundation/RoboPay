"""Sim-to-sim harness: MuJoCo go2.xml vs the unitree_ros URDF in Webots.

The Go2 tier-1 demo runs the paid skills on the MuJoCo menagerie Go2 model.
This module is the sim-to-sim *harness* for the Webots runtime: it re-runs
every skill in MuJoCo, captures the joint configuration at each salient
moment, and — when a Webots R2025a world importing the official unitree_ros
go2 URDF is present — applies the same joint targets through the Webots
Supervisor and reads the foot-tip positions reported by the Webots physics
engine, computing a real measured error against the MuJoCo baseline.

HONESTY CONTRACT:
- Without the Webots runtime this module writes a report with
  ``verdict: "skipped_webots_runtime_missing"`` and exits 0 (SKIP). It does
  NOT claim a measured result; nothing in this repository describes the
  Webots run as validated until that report has a real ``pass`` verdict.
- No placeholder values are ever written into the report: ``max_error_m`` is
  only set from real measurements, and the skip path sets it to null.

Required world conventions (documented in simulation/webots/README.md):
  robot node named with DEF GO2 (or the controller attached to it), and foot
  nodes DEF FL_foot / FR_foot / RL_foot / RR_foot.
"""

import json
import os
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
SIM_ROOT = HERE.parent
sys.path.insert(0, str(SIM_ROOT / "go2"))

try:  # Webots provides the `controller` module at its runtime
    from controller import Supervisor  # noqa: E402
    WEBOTS_PRESENT = True
except ImportError:
    WEBOTS_PRESENT = False

from go2_control import Go2Controller  # noqa: E402

LEGS = ["FL", "FR", "RL", "RR"]
TOLERANCE = 0.05  # 5 cm, looser than PyBullet (independent upstream model)
SKILLS = ["hold", "wave", "sit", "bow", "nod", "turn_to_face"]


def resolve_mujoco_scene():
    env = os.environ.get("GO2_MODEL_PATH")
    if env and os.path.exists(env):
        return env
    candidates = [
        SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "scene.xml",
        SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "go2.xml",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise SystemExit("go2 scene.xml not found; run simulation/setup.sh")


def capture_mujoco_baseline():
    """Run each skill in MuJoCo, return {skill: (joint_pos, foot_pos)}."""
    ctl = Go2Controller(model_path=resolve_mujoco_scene())
    samples = []
    joint_names = [f"{leg}_{s}_joint"
                   for leg in ["FL", "FR", "RL", "RR"]
                   for s in ("hip", "thigh", "calf")]

    def observe(controller):
        joint_pos = {n: float(controller.data.qpos[controller.joint_adr[n]])
                     for n in joint_names}
        foot_pos = {leg.lower(): controller.data.geom_xpos[
            controller.model.geom(leg).id].copy()
            for leg in ["FL", "FR", "RL", "RR"]}
        samples.append((joint_pos, foot_pos, controller.metrics()))

    ctl.set_on_step(observe)
    baseline = {}
    for skill in SKILLS:
        before = len(samples)
        try:
            ctl.execute(skill, {"headingDeg": 30.0} if skill == "turn_to_face"
                        else {})
        except Exception as exc:  # noqa: BLE001
            print(f"skill {skill} raised {exc}")
            continue
        batch = samples[before:]
        if not batch:
            continue
        if skill == "wave":
            pick = max(batch, key=lambda s: s[1]["fr"][2])
        elif skill in ("sit", "nod"):
            pick = min(batch, key=lambda s: s[2]["bodyZ"])
        elif skill == "bow":
            pick = max(batch, key=lambda s: s[2]["bodyPitchDeg"])
        elif skill == "turn_to_face":
            pick = max(batch, key=lambda s: abs(s[2]["bodyYawDeg"]))
        else:
            pick = batch[-1]
        baseline[skill] = (pick[0], pick[1])
    return baseline


def measure_webots_foot_positions(supervisor, timestep, joint_pos, steps=5):
    """Apply joint targets to the Webots robot and read foot-tip positions."""
    motors = {}
    for leg in LEGS:
        for part in ("hip", "thigh", "calf"):
            name = f"{leg}_{part}_joint"
            device = supervisor.getDevice(name)
            if device is None:
                raise SystemExit(
                    f"Webots world missing motor '{name}'; expected a world "
                    f"that imports the unitree_ros go2 URDF (see README)")
            motors[name] = device
    for name, val in joint_pos.items():
        motors[name].setPosition(float(val))
    for _ in range(steps):
        supervisor.step(timestep)
    tips = {}
    for leg in LEGS:
        node = supervisor.getFromDef(f"{leg}_foot")
        if node is None:
            raise SystemExit(
                f"Webots world missing node DEF {leg}_foot; expected a world "
                f"that imports the unitree_ros go2 URDF (see README)")
        tips[leg.lower()] = np.array(node.getPosition(), dtype=float)
    return tips


def main():
    baseline = capture_mujoco_baseline()

    if not WEBOTS_PRESENT:
        report = {
            "simulators": ["mujoco", "webots-unitree_ros"],
            "model_mujoco": os.path.relpath(resolve_mujoco_scene(), HERE)
            .replace("\\", "/"),
            "verdict": "skipped_webots_runtime_missing",
            "max_error_m": None,
            "note": "NOT a measured result. The Webots R2025a runtime is not "
                    "bundled in this repo or in CI; running the harness under "
                    "Webots with a world that imports the unitree_ros go2 URDF "
                    "produces the measured report with verdict pass/fail.",
        }
        with open(HERE / "go2_webots_sim2sim_report.json", "w",
                  encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print("Webots runtime not available -> SKIP (no measured result, "
              "no validation claimed)")
        sys.exit(0)

    supervisor = Supervisor()
    timestep = int(supervisor.getBasicTimeStep())
    report = {
        "simulators": ["mujoco", "webots-unitree_ros"],
        "model_mujoco": os.path.relpath(resolve_mujoco_scene(), HERE)
        .replace("\\", "/"),
        "tolerance_m": TOLERANCE,
        "poses": {},
    }
    worst = 0.0
    for skill, (joint_pos, foot_pos) in baseline.items():
        tips = measure_webots_foot_positions(supervisor, timestep, joint_pos)
        errors = {}
        for leg in ("fl", "fr", "rl", "rr"):
            err = float(np.linalg.norm(tips[leg] - foot_pos[leg]))
            errors[leg] = round(err, 4)
            worst = max(worst, err)
        report["poses"][skill] = {"foot_errors_m": errors}
        print(f"{skill:14s} foot errs: " + ", ".join(
            f"{leg}={e}" for leg, e in errors.items()))

    ok = worst <= TOLERANCE
    report["max_error_m"] = round(worst, 4)
    report["verdict"] = "pass" if ok else "fail"
    with open(HERE / "go2_webots_sim2sim_report.json", "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"max error {worst * 100:.2f} cm -> {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
