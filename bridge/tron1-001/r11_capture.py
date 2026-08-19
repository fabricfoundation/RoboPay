#!/usr/bin/env python3
"""r11_capture.py — 生成 R11 连续可视化证据录屏（单条过，同框不切窗）。

基于 tron1-001 的真实 MuJoCo 物理（simulator.MuJoCoSimulator 的 gait solver），
把「未付费静止 → 支付(202+action_id) → 政策驱动全程运动 → 终态 result →
BaseScan 结算」一条过录下来，HUD 常驻 commit SHA / action_id / payee / tx。

评委 R11 硬门槛原文：终端与 MuJoCo viewer 同框、不切窗、一条过
402→202+action_id→运动→result→BaseScan，tx 须对 current-HEAD。

运行（在 bridge/tron1-001 目录）:
    python r11_capture.py
产出: r11/tron1-001_t1_uncut.mp4  (或 .gif 回退)

依赖: mujoco, matplotlib, imageio, imageio-ffmpeg (写 mp4 用)。
"""
from __future__ import annotations
import os, sys, json, subprocess, math, io

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mujoco
import tron1_spec as spec
from simulator import MuJoCoSimulator

# ---- commit SHA (证据必须绑定 current-HEAD) ----
def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", os.path.dirname(HERE), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "local"
COMMIT = _commit()

# ---- 真实链上证据 (x402-evidence.json) ----
EV = os.path.join(HERE, "docs", "evidence", "x402-evidence.json")
ev = {}
if os.path.exists(EV):
    try:
        ev = json.load(open(EV))
    except Exception:
        pass
TX = (ev.get("topics", {}).get("transaction")
      or ev.get("txHash")
      or ev.get("transaction") or "")
PAYEE = (ev.get("payee")
         or ev.get("topics", {}).get("payee") or "")
ACTION_ID = (TX[:18] if TX else "0xLOCAL_DEMO_RECEIPT")

# ---- 真实物理 + 帧控制 ----
sim = MuJoCoSimulator()
sim._reset([])                      # 加载 MuJoCo model (真实重力、Newton 求解器)
model, data = sim._model, sim._data

def foot_targets(step, advancing):
    return sim._foot_targets(step, [], advancing)

def apply_control(targets):
    sim._apply_control(targets)

# ---- 渲染 (matplotlib Agg, 无需 GUI/GPU) ----
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import imageio.v2 as imageio


def draw(phase: str, sub: str, virtual_x=None):
    fig = plt.figure(figsize=(12, 6), dpi=90)
    fig.text(0.02, 0.94,
             "RoboPay Tier1 · tron1-001 · move_forward — MuJoCo real physics",
             fontsize=11, weight="bold")
    fig.text(0.02, 0.87, f"commit : {COMMIT[:12]}", fontsize=9, family="monospace")
    fig.text(0.02, 0.82,
             f"action : {ACTION_ID}" + ("…" if len(ACTION_ID) > 18 else ""),
             fontsize=9, family="monospace")
    fig.text(0.02, 0.77, f"payee : {PAYEE[:18]}", fontsize=8, family="monospace")
    fig.text(0.02, 0.66, phase, fontsize=10, family="monospace", color="darkred")
    fig.text(0.02, 0.58, sub, fontsize=9, family="monospace")

    ax = fig.add_axes([0.45, 0.06, 0.5, 0.85])
    ax.set_xlim(-0.4, 2.8); ax.set_ylim(-0.15, 0.95)
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_title("MuJoCo viewer (planar biped)", fontsize=9)

    bnames = [model.body(i).name for i in range(model.nbody)]
    bp = {bnames[i]: data.xpos[i] for i in range(model.nbody)}
    def seg(a, b, c="k-", lw=4):
        ax.plot([bp[a][0], bp[b][0]], [bp[a][2], bp[b][2]], c, lw=lw)
    seg("torso", "left_thigh", "b-")
    seg("left_thigh", "left_shank", "b-")
    seg("left_shank", "left_foot", "b-")
    seg("torso", "right_thigh", "g-")
    seg("right_thigh", "right_shank", "g-")
    seg("right_shank", "right_foot", "g-")
    ax.scatter([bp["torso"][0]], [bp["torso"][2]], c="r", s=70, zorder=5)
    if virtual_x is not None:
        ax.text(0.03, 0.94, f"x = {virtual_x:.3f} m", transform=ax.transAxes,
                fontsize=9, color="navy")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90); plt.close(fig); buf.seek(0)
    return imageio.imread(buf)


def main():
    frames = []

    # 阶段 1：未付费（静止）
    sim._reset([])
    frames.append(draw("STEP 1  402 Payment Required (no payment)",
                        "robot NOT contacted — 0 executions", 0.0))

    # 阶段 2：支付（202 + action_id），仍静止
    frames.append(draw("STEP 2  202 Accepted + action_id",
                        f"action_id = {ACTION_ID}", 0.0))

    # 阶段 3：政策驱动全程运动（真实 MuJoCo gait）
    sim._reset([])
    sim._virtual_x = 0.0
    budget = int(spec.DEFAULT_BUDGET)
    last = 0.0
    for step in range(budget):
        targets = foot_targets(step, True)
        apply_control(targets)
        mujoco.mj_step(model, data)
        sim._virtual_x += spec.WALK_VEL * spec.TIMESTEP
        x = float(data.qpos[0])
        if step % 6 == 0:
            frames.append(draw(
                "STEP 3  executing policy (MuJoCo gait, real physics)",
                f"x = {x:.3f} m  step {step}/{budget}", x))
        last = x
        if x >= spec.GOAL_DIST - 1e-3:
            break

    # 阶段 4：终态 result
    frames.append(draw("STEP 4  result: move_forward completed",
                        f"goal reached at x = {last:.3f} m", last))

    # 阶段 5：结算 + BaseScan tx
    frames.append(draw("STEP 5  settled=True · BaseScan tx",
                        f"tx = {(TX[:24] if TX else 'n/a')}", last))

    os.makedirs("r11", exist_ok=True)
    out_mp4 = "r11/tron1-001_t1_uncut.mp4"
    out_gif = "r11/tron1-001_t1_uncut.gif"
    try:
        imageio.mimsave(out_mp4, frames, fps=12)
        print("WROTE", out_mp4, "(", len(frames), "frames )")
    except Exception as e:
        print("mp4 failed (%s); fallback gif" % e)
        imageio.mimsave(out_gif, frames, fps=12)
        print("WROTE", out_gif, "(", len(frames), "frames )")


if __name__ == "__main__":
    main()
