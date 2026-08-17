"""Run every claim in the validation report and print the measured result.

Nothing in docs/validation-report.md is asserted by hand. This script produces
the numbers that go in it, so re-running it is how a reviewer checks that the
report still matches the code:

    python -m sim_bridge.tools.collect_evidence --json > evidence.json

It exercises the payment gate in-process (no Zenoh needed) and then runs the
sim-to-sim comparison, which is the slow part.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import random

from ..x2.action_contract import ActionEnvelope, canonical_params_hash
from ..x2.mapper import (
    GOAL_X,
    GOAL_Y,
    MAX_PUSH,
    MIN_PUSH,
    PUCK_X,
    PUCK_Y,
    TaskSpec,
    catalogue,
)
from ..x2.node import ActionNode, IdempotencyStore
from ..simulation.metrics import compare
from ..simulation.runner import TaskRunner

ROBOT = "x2-sim-001"

#: Seed for the sampled targets. Fixed so the evidence is reproducible: a
#: reviewer re-running this script gets the same task list, and a regression
#: shows up as a changed verdict rather than as a different sample.
GRID_SEED = 7


def sample_grid(count: int = 10, seed: int = GRID_SEED) -> list[tuple[float, ...]]:
    """Draw target pairs uniformly from the advertised envelope.

    Sampled rather than written out, because a hand-picked list is exactly the
    thing that flatters a policy: an earlier version of this work reported 5 of
    8 on targets that had been chosen while debugging, and 3 of 16 on a neutral
    grid. Drawing from the same bounds the skill advertises means the reported
    success rate is the one a payer would actually see.
    """
    rng = random.Random(seed)
    grid: list[tuple[float, ...]] = []
    while len(grid) < count:
        px = round(rng.uniform(PUCK_X.low, PUCK_X.high), 4)
        py = round(rng.uniform(PUCK_Y.low, PUCK_Y.high), 4)
        gx = round(rng.uniform(GOAL_X.low, GOAL_X.high), 4)
        gy = round(rng.uniform(GOAL_Y.low, GOAL_Y.high), 4)
        if MIN_PUSH <= ((gx - px) ** 2 + (gy - py) ** 2) ** 0.5 <= MAX_PUSH:
            grid.append((px, py, gx, gy))
    return grid


GRID = sample_grid()


def envelope(
    skill: str,
    params: dict[str, Any],
    key: str,
    *,
    paid: bool = True,
    tamper: bool = False,
    expires: str | None = None,
) -> ActionEnvelope:
    body: dict[str, Any] = {
        "actionId": f"act_{key}",
        "robotId": ROBOT,
        "skillId": skill,
        "params": dict(params),
        "idempotencyKey": key,
        "paramsHash": canonical_params_hash(params),
        "payment": {
            "provider": "x402",
            "amount": "10000",
            "asset": "USDC",
            "network": "eip155:84532",
            "verified": paid,
            **({"txHash": "0x" + "ab" * 32} if paid else {}),
        },
    }
    if tamper and "goal_x" in body["params"]:
        # Shifted by less than the width of the goal_x band, so the tampered
        # value is still a legal parameter. The action must be refused for the
        # hash, not for landing out of range -- otherwise this proves nothing
        # about tamper detection.
        body["params"]["goal_x"] += 0.02
    if expires:
        body["expiresAt"] = expires
    return ActionEnvelope.from_json(body)


def payment_gate_evidence() -> list[dict[str, Any]]:
    """Each acceptance rule, with the code and settle flag it produced."""
    node = ActionNode(ROBOT, TaskRunner(), IdempotencyStore())
    px, py, gx, gy = GRID[0]
    good = {"puck_x": px, "puck_y": py, "goal_x": gx, "goal_y": gy}
    rows: list[dict[str, Any]] = []

    def record(label: str, result: Any) -> None:
        rows.append({
            "case": label,
            "status": result.status,
            "code": (result.error or {}).get("code"),
            "settle": result.settle,
            "replayed": result.replayed,
            "displacementM": (result.metrics or {}).get("displacementM"),
        })

    record("unpaid request",
           node.handle(envelope("push_to_target", good, "e-unpaid", paid=False)))
    record("tampered params",
           node.handle(envelope("push_to_target", good, "e-tamper", tamper=True)))
    record("expired action",
           node.handle(envelope("push_to_target", good, "e-expired",
                                expires="2020-01-01T00:00:00+00:00")))
    record("out-of-range params",
           node.handle(envelope("push_to_target", dict(good, puck_y=-0.90),
                                "e-range")))
    record("wrong robot id", node.handle(
        ActionEnvelope.from_json({
            **json.loads(json.dumps(envelope("push_to_target", good, "e-robot").raw)),
            "robotId": "some-other-robot",
        })
    ))
    record("deliberate failure skill",
           node.handle(envelope("diagnostic_fail", {}, "e-fail")))
    record("free stop skill",
           node.handle(envelope("stop", {}, "e-stop", paid=False)))
    record("valid paid action",
           node.handle(envelope("push_to_target", good, "e-ok")))
    record("replay of the same key",
           node.handle(envelope("push_to_target", good, "e-ok")))
    return rows


def workspace_evidence() -> list[dict[str, Any]]:
    runner = TaskRunner()
    rows = []
    for px, py, gx, gy in GRID:
        metrics = runner.run(
            TaskSpec("push_to_target", puck_xy=(px, py), goal_xy=(gx, gy))
        )
        rows.append({
            "puck": [px, py],
            "goal": [gx, gy],
            "success": metrics.success,
            "reason": metrics.reason,
            "displacementM": round(metrics.displacement, 4),
            "finalDistanceM": round(metrics.final_distance, 4),
            "simSeconds": round(metrics.sim_seconds, 2),
        })
    return rows


def sim2sim_evidence(cases: int = 3) -> list[dict[str, Any]]:
    from ..simulation.sim2sim import run_both

    rows = []
    for px, py, gx, gy in GRID[:cases]:
        mj, dk = run_both(
            TaskSpec("push_to_target", puck_xy=(px, py), goal_xy=(gx, gy))
        )
        rows.append({
            "puck": [px, py],
            "goal": [gx, gy],
            "mujoco": {"success": mj.success,
                       "displacementM": round(mj.displacement, 4),
                       "finalDistanceM": round(mj.final_distance, 4)},
            "drake": {"success": dk.success,
                      "displacementM": round(dk.displacement, 4),
                      "finalDistanceM": round(dk.final_distance, 4)},
            "comparison": compare(mj, dk),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--sim2sim-cases", type=int, default=3)
    parser.add_argument("--skip-workspace", action="store_true")
    args = parser.parse_args(argv)

    evidence: dict[str, Any] = {
        "robotId": ROBOT,
        "catalogue": catalogue(ROBOT),
        "paymentGate": payment_gate_evidence(),
    }
    if not args.skip_workspace:
        evidence["workspace"] = workspace_evidence()
    evidence["simToSim"] = sim2sim_evidence(args.sim2sim_cases)

    if args.json:
        print(json.dumps(evidence, indent=2))
        return 0

    print("== payment gate ==")
    for row in evidence["paymentGate"]:
        print(f"  {row['case']:<26} status={row['status']:<8} "
              f"code={str(row['code']):<22} settle={row['settle']}")
    if "workspace" in evidence:
        ok = sum(1 for r in evidence["workspace"] if r["success"])
        print(f"\n== workspace ({ok}/{len(evidence['workspace'])} delivered) ==")
        for row in evidence["workspace"]:
            mark = "ok  " if row["success"] else "FAIL"
            print(f"  {mark} puck {row['puck']} -> goal {row['goal']}  "
                  f"moved {row['displacementM']}m  left {row['finalDistanceM']}m")
    print("\n== sim-to-sim ==")
    for row in evidence["simToSim"]:
        c = row["comparison"]
        print(f"  puck {row['puck']} -> goal {row['goal']}: "
              f"mujoco={row['mujoco']['success']} drake={row['drake']['success']} "
              f"gap={c['puckEndGapM']}m tol={c['toleranceM']}m agrees={c['agrees']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
