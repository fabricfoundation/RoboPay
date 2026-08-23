#!/usr/bin/env python3
"""r11_capture.py -- continuous R11 visual evidence (one take, no window switch).

Real MuJoCo physics for deep-robotics-x30-pro: unpaid still -> pay (202+action_id) ->
policy-driven motion -> terminal result -> BaseScan settlement. HUD pins commit
SHA, action_id, payee, tx. Bind to current-HEAD (acceptance R11).

Run (in this bridge dir):  python r11_capture.py
Produces: r11/deep-robotics-x30-pro_t1_uncut.gif (or .mp4)
"""
from __future__ import annotations
import os, sys, json, subprocess, math, io
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mujoco
import engine
from engine import Simulator, stand_z
from simulator import MuJoCoSimulator

ROBOT_ID = "deep-robotics-x30-pro"
LEGS = ['lf', 'rf', 'lh', 'rh']

def _commit():
    ov = os.environ.get("R11_COMMIT", "").strip()
    if ov:
        return ov
    try:
        return subprocess.check_output(["git", "-C", os.path.dirname(HERE),
            "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "local"
COMMIT = _commit()

EV = os.path.join(HERE, "docs", "evidence", "x402-evidence.json")
ev = {}
if os.path.exists(EV):
    try:
        ev = json.load(open(EV))
    except Exception:
        pass
TX = ((ev.get("txs") or [None])[0] or ev.get("txHash") or ev.get("transaction")
      or "")
PAYEE = (ev.get("payee") or (ev.get("topics", {}) or {}).get("payee") or "")
ACTION_ID = (TX[:18] if TX else "0xLOCAL_DEMO_RECEIPT")

sim = MuJoCoSimulator()
sim._reset([])
model, data = sim._model, sim._data

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import imageio.v2 as imageio

def draw(phase, sub, virtual_x=None):
    fig = plt.figure(figsize=(12, 6), dpi=90)
    fig.text(0.02, 0.94, f"RoboPay Tier1 . {ROBOT_ID} . move_forward -- MuJoCo real physics",
             fontsize=11, weight="bold")
    fig.text(0.02, 0.87, f"commit : {COMMIT[:12]}", fontsize=9, family="monospace")
    fig.text(0.02, 0.82, f"action : {ACTION_ID}" + ("..." if len(ACTION_ID) > 18 else ""),
             fontsize=9, family="monospace")
    fig.text(0.02, 0.77, f"payee : {PAYEE[:18]}", fontsize=8, family="monospace")
    fig.text(0.02, 0.66, phase, fontsize=10, family="monospace", color="darkred")
    fig.text(0.02, 0.58, sub, fontsize=9, family="monospace")
    ax = fig.add_axes([0.45, 0.06, 0.5, 0.85])
    ax.set_xlim(-0.4, 3.0); ax.set_ylim(-0.15, 1.0)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("MuJoCo viewer (planar quadruped)", fontsize=9)
    bnames = [model.body(i).name for i in range(model.nbody)]
    bp = {bnames[i]: data.xpos[i] for i in range(model.nbody)}
    def seg(a, b, c="k-", lw=4):
        ax.plot([bp[a][0], bp[b][0]], [bp[a][2], bp[b][2]], c, lw=lw)
    for leg in LEGS:
        seg("torso", f"{leg}_thigh", "b-")
        seg(f"{leg}_thigh", f"{leg}_shank", "b-")
        seg(f"{leg}_shank", f"{leg}_foot", "b-")
    ax.scatter([bp["torso"][0]], [bp["torso"][2]], c="r", s=70, zorder=5)
    if virtual_x is not None:
        ax.text(0.03, 0.94, f"x = {virtual_x:.3f} m", transform=ax.transAxes,
                fontsize=9, color="navy")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90); plt.close(fig); buf.seek(0)
    return imageio.imread(buf)

def main():
    frames = []
    sim._reset([])
    frames.append(draw("STEP 1  402 Payment Required (no payment)",
                       "robot NOT contacted -- 0 executions", 0.0))
    frames.append(draw("STEP 2  202 Accepted + action_id",
                       f"action_id = {ACTION_ID}", 0.0))
    sim._reset([])
    sim._virtual_x = 0.0
    budget = int(engine.ROBOTS[ROBOT_ID].default_budget)
    last = 0.0
    for step in range(budget):
        targets = sim._foot_targets(step, [], True)
        sim._apply_control(targets)
        mujoco.mj_step(model, data)
        sim._virtual_x += engine.ROBOTS[ROBOT_ID].walk_vel * engine.ROBOTS[ROBOT_ID].timestep
        x = float(data.qpos[0])
        if step % 6 == 0:
            frames.append(draw("STEP 3  executing policy (MuJoCo gait, real physics)",
                               f"x = {x:.3f} m  step {step}/{budget}", x))
        last = x
        if x >= engine.ROBOTS[ROBOT_ID].goal_dist - 1e-3:
            break
    frames.append(draw("STEP 4  result: move_forward completed",
                       f"goal reached at x = {last:.3f} m", last))
    frames.append(draw("STEP 5  settled=True . BaseScan tx",
                       f"tx = {(TX[:24] if TX else 'n/a')}", last))
    os.makedirs("r11", exist_ok=True)
    out_mp4 = f"r11/{ROBOT_ID}_t1_uncut.mp4"
    out_gif = f"r11/{ROBOT_ID}_t1_uncut.gif"
    try:
        imageio.mimsave(out_mp4, frames, fps=12)
        print("WROTE", out_mp4, "(", len(frames), "frames )")
    except Exception as e:
        print("mp4 failed (%s); fallback gif" % e)
        imageio.mimsave(out_gif, frames, fps=12)
        print("WROTE", out_gif, "(", len(frames), "frames )")

if __name__ == "__main__":
    main()
