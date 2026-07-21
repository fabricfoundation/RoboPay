"""IK self-check: round-trip FK->IK, then cross-check FK against MuJoCo."""

import sys
import numpy as np
import mujoco

from go2_gait import leg_ik, foot_fk, LEG_SIGNS, LEG_NAMES

MODEL = "../models/mujoco_menagerie/unitree_go2/scene.xml"

rng = np.random.default_rng(0)
model = mujoco.MjModel.from_xml_path(MODEL)
data = mujoco.MjData(model)

max_rt, max_fk = 0.0, 0.0
for _ in range(200):
    q_abd = rng.uniform(-0.8, 0.8)
    q_thigh = rng.uniform(0.2, 1.6)
    q_calf = rng.uniform(-2.4, -1.0)
    for leg in range(4):
        side = LEG_SIGNS[leg][1]
        # 1) round trip through our own FK/IK
        x, y, z = foot_fk(q_abd, q_thigh, q_calf, side)
        qa, qt, qc = leg_ik(x, y, z, side)
        max_rt = max(max_rt, abs(qa - q_abd), abs(qt - q_thigh), abs(qc - q_calf))
        # 2) our FK vs mujoco kinematics (foot geom center rel. hip joint)
        data.qpos[:] = 0
        data.qpos[3] = 1  # identity quat
        data.qpos[7 + leg * 3: 10 + leg * 3] = [q_abd, q_thigh, q_calf]
        mujoco.mj_kinematics(model, data)
        foot = data.geom(LEG_NAMES[leg]).xpos
        hip = data.joint(f"{LEG_NAMES[leg]}_hip_joint").xanchor
        err = np.linalg.norm(np.array([x, y, z]) - (foot - hip))
        max_fk = max(max_fk, err)

print(f"round-trip max joint err: {max_rt:.2e} rad")
print(f"FK vs mujoco max foot err: {max_fk * 1000:.2f} mm")
ok = max_rt < 1e-6 and max_fk < 0.01
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
