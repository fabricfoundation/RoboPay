"""Gait acceptance test: stand -> walk straight -> turn in place -> arc.

Headless. Prints per-phase metrics as JSON and PASS/FAIL.
Fail conditions anywhere: trunk below 0.15 m, |roll| or |pitch| > 0.6 rad.
"""

import json
import math
import sys

import numpy as np
import mujoco

from go2_gait import TrotController

MODEL = "../models/mujoco_menagerie/unitree_go2/scene.xml"


def rpy(qpos):
    w, x, y, z = qpos[3:7]
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return roll, pitch, yaw


def run_phase(model, data, ctrl, cmd, duration, name):
    ctrl.set_command(*cmd)
    n = int(duration / model.opt.timestep)
    start_pos = data.qpos[:2].copy()
    start_yaw = rpy(data.qpos)[2]
    yaw_acc, prev_yaw = 0.0, start_yaw
    min_z, max_tilt = 1e9, 0.0
    for _ in range(n):
        data.ctrl[:] = ctrl.compute(data.qpos, data.qvel)
        mujoco.mj_step(model, data)
        r, p, yw = rpy(data.qpos)
        min_z = min(min_z, data.qpos[2])
        max_tilt = max(max_tilt, abs(r), abs(p))
        d = yw - prev_yaw
        yaw_acc += math.atan2(math.sin(d), math.cos(d))  # unwrap
        prev_yaw = yw
    dist = float(np.linalg.norm(data.qpos[:2] - start_pos))
    return {
        "phase": name, "cmd": list(cmd), "duration_s": duration,
        "distance_m": round(dist, 3),
        "yaw_change_deg": round(math.degrees(yaw_acc), 1),
        "min_trunk_z_m": round(float(min_z), 3),
        "max_tilt_rad": round(float(max_tilt), 3),
        "upright": bool(min_z > 0.15 and max_tilt < 0.6),
    }


def main():
    model = mujoco.MjModel.from_xml_path(MODEL)
    data = mujoco.MjData(model)
    mujoco.mj_resetDataKeyframe(model, data, 0)  # "home" keyframe
    ctrl = TrotController(model.opt.timestep)

    results = [
        run_phase(model, data, ctrl, (0.0, 0.0, 0.0), 2.0, "stand"),
        run_phase(model, data, ctrl, (0.4, 0.0, 0.0), 5.0, "walk_forward"),
        run_phase(model, data, ctrl, (0.0, 0.0, 1.0), 3.0, "turn_in_place"),
        run_phase(model, data, ctrl, (0.3, 0.0, 0.5), 4.0, "arc"),
    ]
    checks = {
        "stand_upright": results[0]["upright"] and results[0]["distance_m"] < 0.1,
        "walk_distance": results[1]["upright"] and results[1]["distance_m"] >= 1.5,
        "walk_heading": abs(results[1]["yaw_change_deg"]) < 30,
        "turn_yaw": results[2]["upright"] and abs(results[2]["yaw_change_deg"]) >= 90,
        "arc_upright": results[3]["upright"],
    }
    print(json.dumps({"phases": results, "checks": checks}, indent=1))
    ok = all(checks.values())
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
