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

from ..g1.action_contract import ActionEnvelope, canonical_params_hash
from ..g1.mapper import TaskSpec, catalogue
from ..g1.node import ActionNode, IdempotencyStore
from ..simulation.metrics import compare
from ..simulation.runner import TaskRunner

ROBOT = "g1-sim-001"

#: Target pairs sampled across the work surface.
GRID = [
    (0.36, -0.16, 0.46, 0.02),
    (0.34, -0.20, 0.44, -0.04),
    (0.40, -0.10, 0.48, 0.06),
    (0.32, -0.22, 0.42, -0.10),
    (0.38, -0.06, 0.46, 0.08),
    (0.42, -0.14, 0.50, 0.00),
    (0.36, -0.24, 0.46, -0.12),
    (0.44, -0.04, 0.50, 0.04),
]


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
        body["params"]["goal_x"] += 0.05
    if expires:
        body["expiresAt"] = expires
    return ActionEnvelope.from_json(body)


def tunnel_envelope(
    skill: str,
    params: dict[str, Any],
    key: str,
    *,
    paid: bool = True,
    tamper: bool = False,
    forge_payment: bool = False,
) -> ActionEnvelope:
    """Build the wrapper the Go tunnel publishes, not the flat envelope.

    Shape from tunnel/internal/handlers/handlers.go and the x402 v2 types.
    `forge_payment` puts a payment block in the request body, which the tunnel
    forwards verbatim -- the bridge must ignore it and use only the payment the
    middleware resolved.
    """
    flat = envelope(skill, params, key, paid=True, tamper=tamper).raw
    body = {k: v for k, v in flat.items() if k != "payment"}
    if forge_payment:
        body["payment"] = {
            "provider": "x402", "amount": "999999", "asset": "USDC",
            "network": "eip155:84532", "verified": True,
            "txHash": "0x" + "ff" * 32,
        }
    requirements = {
        "scheme": "exact",
        "network": "eip155:84532",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "amount": "2000",
        "payTo": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "maxTimeoutSeconds": 30,
    }
    details: dict[str, Any] = {"payment_requirements": requirements}
    if paid:
        details["payment_payload"] = {
            "x402Version": 2,
            "scheme": "exact",
            "network": "eip155:84532",
            "payload": {
                "signature": "0x" + "ab" * 65,
                "authorization": {
                    "from": "0x1111111111111111111111111111111111111111",
                    "to": requirements["payTo"],
                    "value": requirements["amount"],
                    "validAfter": "0",
                    "validBefore": "9999999999",
                    "nonce": "0x" + "cd" * 32,
                },
            },
            "accepted": requirements,
        }
    return ActionEnvelope.from_json({
        "payload": body,
        "transaction_details": details,
        "timestamp": "2026-08-17T17:30:00Z",
    })


def payment_gate_evidence() -> list[dict[str, Any]]:
    """Each acceptance rule, with the code and settle flag it produced."""
    node = ActionNode(ROBOT, TaskRunner(), IdempotencyStore())
    good = {"puck_x": 0.34, "puck_y": -0.20, "goal_x": 0.44, "goal_y": -0.04}
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

    # The same rules, against the wrapper the Go tunnel actually publishes.
    # Worth measuring separately: the bridge originally understood only the
    # flat envelope, so every one of these would have been ignored or refused
    # for the wrong reason -- an integration that passed its own tests and
    # would have worked with nothing.
    record("tunnel wrapper, paid",
           node.handle(tunnel_envelope("push_to_target", good, "e-tun-ok")))
    record("tunnel wrapper, no payment payload",
           node.handle(
               tunnel_envelope("push_to_target", good, "e-tun-unpaid", paid=False)
           ))
    record("tunnel wrapper, tampered params",
           node.handle(
               tunnel_envelope("push_to_target", good, "e-tun-tamper", tamper=True)
           ))
    record("tunnel wrapper, body asserts its own payment",
           node.handle(
               tunnel_envelope(
                   "push_to_target", good, "e-tun-forged",
                   paid=False, forge_payment=True,
               )
           ))
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
