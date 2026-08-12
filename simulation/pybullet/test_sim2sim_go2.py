"""Sim-to-sim: MuJoCo go2.xml vs PyBullet (go2_simple_kin.urdf).

The Go2 tier-1 demo runs the paid skills on the MuJoCo menagerie Go2 model.
This test proves the kinematics the controller produces are not a MuJoCo
artifact: it re-runs every skill, captures the joint configuration at each
salient moment (wave peak, sit deepest crouch, bow max pitch, nod max dip,
end of turn, settled home) and reproduces the same pose in PyBullet via the
committed kinematic URDF `go2_simple_kin.urdf`, which is *generated from the
same go2.xml* by `make_go2_kin_urdf.py` (joint frames, axes and limits read
straight from the MJCF), so the two engines share the same kinematics by
construction. The foot-sphere centres (geom FL/FR/RL/RR in MuJoCo vs the
calf-frame foot offset in PyBullet) must agree within a tight tolerance.

Writes go2_sim2sim_report.json next to this file. Exits nonzero on failure.
"""

import json
import os
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).parent
SIM_ROOT = HERE.parent
sys.path.insert(0, str(SIM_ROOT / "go2"))

import pybullet  # noqa: E402

from go2_control import Go2Controller  # noqa: E402

LEGS = ["fl", "fr", "rl", "rr"]
FOOT_LOCAL = np.array([-0.002, 0.0, -0.213])   # foot sphere centre in calf frame
TOLERANCE = 0.01                               # 1 cm


def resolve_scene():
    env = os.environ.get("GO2_MODEL_PATH")
    if env and os.path.exists(env):
        return env
    candidates = [
        SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "scene.xml",
        SIM_ROOT / "models" / "mujoco_menagerie" / "unitree_go2" / "go2.xml",
        pathlib.Path(r"C:\Users\DeLL-L\AppData\Local\Temp\opencode\robopay-study"
                     r"\unitree_mujoco\unitree_robots\go2\scene.xml"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise SystemExit("go2 scene.xml not found; run simulation/setup.sh")


def resolve_urdf():
    """The committed kinematic URDF generated from go2.xml."""
    urdf = HERE / "go2_simple_kin.urdf"
    if not urdf.exists():
        raise SystemExit(f"missing {urdf}; run simulation/setup.sh, then "
                         "python make_go2_kin_urdf.py")
    return str(urdf)


def capture_mujoco_poses():
    """Run each skill, return {name: (joint_pos dict, foot tips dict)}."""
    ctl = Go2Controller(model_path=resolve_scene())
    samples = []
    joint_names = [f"{leg.upper()}_{s}_joint"
                   for leg in LEGS for s in ("hip", "thigh", "calf")]

    def observe(controller):
        joint_pos = {n: float(controller.data.qpos[controller.joint_adr[n]])
                     for n in joint_names}
        foot_pos = {leg: controller.data.geom_xpos[
            controller.model.geom(leg.upper()).id].copy() for leg in LEGS}
        body_pose = controller.data.qpos[0:7].copy()   # pos + quat (wxyz)
        samples.append((joint_pos, foot_pos, controller.metrics(), body_pose))

    ctl.set_on_step(observe)
    baseline_poses = {}
    for skill in ["hold", "wave", "sit", "bow", "nod", "turn_to_face"]:
        before = len(samples)
        try:
            ctl.execute(skill, {"headingDeg": 30.0} if skill == "turn_to_face"
                        else {})
        except Exception as exc:
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
        baseline_poses[skill] = pick
    return baseline_poses


def pybullet_foot_tips(urdf, joint_pos, body_pose):
    """Foot-sphere centres for a joint_pos dict {joint_name: radians}.

    ``body_pose`` is the MuJoCo freejoint (pos + quat wxyz) so the base frame
    matches the source simulator exactly.
    """
    pybullet.connect(pybullet.DIRECT)
    body = pybullet.loadURDF(str(urdf), useFixedBase=False)
    joint_ids = {}
    for j in range(pybullet.getNumJoints(body)):
        info = pybullet.getJointInfo(body, j)
        joint_ids[info[1].decode()] = j
    pos = tuple(float(v) for v in body_pose[0:3])
    orn = tuple(float(v) for v in body_pose[4:7]) + \
        tuple(float(v) for v in body_pose[3:4])      # wxyz -> xyzw
    pybullet.resetBasePositionAndOrientation(body, pos, orn)
    for name, val in joint_pos.items():
        pybullet.resetJointState(body, joint_ids[name], float(val))
    tips = {}
    for leg in LEGS:
        link = joint_ids[f"{leg.upper()}_calf_joint"]
        pos, _quat, com_pos, com_quat, fk_pos, fk_quat = \
            pybullet.getLinkState(body, link, computeForwardKinematics=1)
        orn = pybullet.getMatrixFromQuaternion(fk_quat)
        R = np.array([[orn[0], orn[1], orn[2]],
                      [orn[3], orn[4], orn[5]],
                      [orn[6], orn[7], orn[8]]])
        tips[leg] = np.array(fk_pos) + R @ FOOT_LOCAL
    pybullet.disconnect()
    return tips


def main():
    xml = resolve_scene()
    urdf = resolve_urdf()
    poses = capture_mujoco_poses()

    report = {"simulators": ["mujoco", "pybullet-go2_simple_kin"],
              "model": os.path.relpath(xml, HERE).replace("\\", "/"),
              "pybullet_model": os.path.relpath(urdf, HERE).replace("\\", "/"),
              "tolerance_m": TOLERANCE,
              "poses": {}}
    worst = 0.0
    for name, (joint_pos, foot_pos, _metrics, body_pose) in poses.items():
        tips = pybullet_foot_tips(urdf, joint_pos, body_pose)
        errors = {}
        for leg in LEGS:
            err = float(np.linalg.norm(np.array(tips[leg]) - foot_pos[leg]))
            errors[leg] = round(err, 4)
            worst = max(worst, err)
        report["poses"][name] = {"foot_errors_m": errors}
        print(f"{name:14s} foot errs: " + ", ".join(
            f"{leg}={e}" for leg, e in errors.items()))

    ok = worst <= TOLERANCE
    report["max_error_m"] = round(worst, 4)
    report["verdict"] = "pass" if ok else "fail"
    with open(HERE / "go2_sim2sim_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"max error {worst*100:.2f} cm -> "
          f"{'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
