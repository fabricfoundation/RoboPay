"""End-to-end demo client for unitree-g1 planar biped (Tier 1).

No LLM, no agent, no hidden state -- a plain CLI that walks the paid flow and
prints every step so a reviewer can read the evidence in one screen:

    1  discover skills        (free, from profiles/skills.yaml)
    2  request action unpaid  -> HTTP 402 + x402 accepts block
    3  robot NOT contacted    (proved by the execution counter)
    4  pay                    -> challenge-matched receipt
    5  submit paid action     -> six-field envelope
    6  publish                -> robot/tunnel/action
    7  execute                -> MuJoCo / PyBullet physics (real gait)
    8  publish                -> robot/tunnel/result
    9  settle or skip         -> settlement only when execution succeeded
    10 replay the key         -> rejected, no re-execution, no re-settlement

The payment receipt used here is a *challenge-matched protocol receipt*: it
satisfies the x402 verifier (amount / network / asset / well-formed txHash /
no replay) so the gate can be exercised end-to-end. It is explicitly NOT a
real on-chain transaction -- the genuine Base Sepolia settlement (tx hash,
block, payer, payee) lives in x402-evidence.json, which is the artifact a
reviewer should inspect for on-chain proof.

Usage
    python -m flow.demo                          # single happy path (MuJoCo)
    python -m flow.demo --skill pick_and_carry
    python -m flow.demo --skill move_forward
    python -m flow.demo --skill navigate_obstacle
    python -m flow.demo --all                    # all scenes + summary
    python -m flow.demo --engine pybullet        # second physics engine
    python -m flow.demo --transport zenoh        # real Zenoh (Linux/macOS)
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from flow.executor import SimExecutor
from flow.relay import Relay
from flow.zenoh_transport import (ACTION_TOPIC, RESULT_TOPIC, LoopbackTransport,
                                  ZenohRobotNode, ZenohTransport, has_zenoh)

try:
    from flow import profiles
except Exception:                                            # pragma: no cover
    profiles = None

ROBOT_ID = "unitree-g1"

# (skill_id, params) -- the genuine outcomes of the paid flow across every
# skill: success (locomotion + pick-and-carry + safe hold) and genuine-physics
# timeout (a goal the gait cannot reach inside the step budget).
DEMO_SCENES = [
    ("move_forward", {}),                        # success
    ("navigate_obstacle", {}),                   # success (steps over the curb)
    ("pick_and_carry", {}),                      # success
    ("stop", {}),                                # success (safe hold)
    ("move_forward", {"goalDistance": 8.0}),     # budget exhausts -> timeout
    ("pick_and_carry", {"dropDistance": 8.0}),   # budget exhausts -> timeout
    ("navigate_obstacle", {"goal_x": 8.0}),      # budget exhausts -> timeout
]


def step(n: int, title: str) -> None:
    print(f"\n[{n:2d}] {title}")


def dump(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=False)


def fake_receipt(accepts: dict, scene: str, n: int) -> dict:
    """A challenge-matched protocol receipt for exercising the payment gate.

    Honest: this is NOT an on-chain tx. It merely satisfies the x402 verifier
    so the demo can show 402 -> pay -> execute -> settle. Real settlement is
    in x402-evidence.json.
    """
    return {
        "scheme": accepts.get("scheme", "exact"),
        "network": accepts.get("network", "eip155:84532"),
        "asset": accepts.get("asset"),
        "amount": accepts.get("amount"),
        "payer": f"0xDEMOPAYER{abs(hash(scene)) % 10**36:036x}",
        "txHash": "0x" + f"{abs(hash(f'{scene}-{n}')):064x}"[:64],
    }


class CountingExecutor(SimExecutor):
    """Same executor, plus a counter so the demo can PROVE no free execution."""

    def __init__(self, engine: str = "mujoco"):
        super().__init__(engine)
        self.calls = 0

    def execute(self, skill_id: str, params: dict):
        self.calls += 1
        return super().execute(skill_id, params)


def build_relay(engine: str, transport_name: str):
    executor = CountingExecutor(engine)
    if transport_name == "zenoh":
        if not has_zenoh():
            raise SystemExit(
                "zenoh is not installed on this platform (no Windows wheels).\n"
                "Run with --transport loopback, or use Linux / the CI workflow."
            )
        node = ZenohRobotNode(executor)
        node.serve_background() if hasattr(node, "serve_background") else None
        transport = ZenohTransport()
        return Relay(transport=transport), executor, node
    return Relay(transport=LoopbackTransport(executor)), executor, None


def run_once(relay: Relay, executor_probe, skill_id: str, params: dict,
             verbose: bool = True) -> dict:
    key = f"demo-{skill_id}-{int(time.time() * 1000)}"
    request = {"robotId": ROBOT_ID, "skill": skill_id,
               "params": params, "idempotencyKey": key}

    if verbose:
        step(2, f"request_action skill={skill_id} params={params} (no payment)")
    challenge = relay.handle(dict(request))
    if verbose:
        print(dump(challenge))
        step(3, "robot contacted so far: "
                f"{getattr(executor_probe, 'calls', 0)} executions  <- must be 0")

    accepts = (challenge.get("accepts") or [{}])[0]
    if verbose:
        step(4, f"pay {accepts.get('amount')} {accepts.get('currency')} "
                f"on {accepts.get('network')}")
        print("     note: this is a challenge-matched protocol receipt for the "
              "demo.\n           Real on-chain settlement is in x402-evidence.json.")

    receipt = fake_receipt(accepts, skill_id, 1)
    if verbose:
        print(f"     txHash = {receipt['txHash'][:18]}... (local, not on-chain)")

    if verbose:
        step(5, "submit_paid_action  (six-field envelope + X-PAYMENT receipt)")
        step(6, f"publish -> {ACTION_TOPIC}")
        step(7, "execute -> physics (real MuJoCo/PyBullet gait)")
    result = relay.handle({**request, "payment": receipt})
    if verbose:
        step(8, f"result  <- {RESULT_TOPIC}")
        print(dump(result))

    if verbose:
        verdict = "SETTLED" if result.get("settled") else "NOT SETTLED"
        step(9, f"payment {result.get('paymentState')} -> {verdict}")
        step(10, "replay the same idempotencyKey")
        replay = relay.handle({**request, "payment": receipt})
        print(dump(replay))
        print(f"     executions total: {getattr(executor_probe, 'calls', '?')} "
              "<- must be 1")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="unitree-g1 paid-flow demo")
    ap.add_argument("--skill", default="pick_and_carry",
                    choices=["move_forward", "navigate_obstacle",
                             "pick_and_carry", "stop"])
    ap.add_argument("--engine", default="mujoco", choices=["mujoco", "pybullet"])
    ap.add_argument("--transport", default="loopback", choices=["loopback", "zenoh"])
    ap.add_argument("--all", action="store_true", help="run every scene")
    args = ap.parse_args(argv)

    print("=" * 68)
    print(f" RoboPay Tier 1 demo -- {ROBOT_ID} / planar biped")
    print(f" engine={args.engine}  transport={args.transport}")
    print("=" * 68)

    step(1, "list_skills (free discovery)")
    if profiles is not None:
        catalogue = profiles.list_skills(ROBOT_ID)
        for s in catalogue["skills"]:
            print(f"     {s['skillId']}: {s['price']} {s['currency']} "
                  f"on {s['network']} ({s['settlement']})")
    else:
        print("     profiles unavailable (pyyaml not installed)")

    if args.all:
        rows = []
        for skill_id, params in DEMO_SCENES:
            relay, executor, node = build_relay(args.engine, args.transport)
            print("\n" + "-" * 68)
            print(f" scene: {skill_id} {params}")
            print("-" * 68)
            res = run_once(relay, executor, skill_id, params, verbose=False)
            m = res.get("metrics") or {}
            print(f" status={res.get('status')}  msg={res.get('message')}  "
                  f"settled={res.get('settled')}")
            print(f" distance={m.get('distanceTraveled')} m  "
                  f"steps={m.get('stepsUsed')}/{m.get('stepBudget')}  "
                  f"reached={m.get('reached')}  "
                  f"carried={m.get('carried')}")
            rows.append((skill_id, params, res.get("status"), res.get("settled"),
                         m.get("distanceTraveled", 0.0),
                         m.get("stepsUsed", 0), m.get("reached", False)))
            if node:
                node.stop()
        print("\n" + "=" * 78)
        print(f" {'skill':<18}{'status':<11}{'settled':>8}"
              f"{'dist(m)':>10}{'steps':>8}")
        print("-" * 78)
        for skill_id, params, status, settled, dist, steps, reached in rows:
            p = f" {params}" if params else ""
            print(f" {skill_id + p:<18}{status:<11}{str(settled):>8}"
                  f"{dist:>10.4f}{steps:>8}")
        print("=" * 78)
        # success scenes (move_forward, navigate_obstacle, pick_and_carry,
        # stop) settle; the three genuine timeouts must NOT settle.
        success_idx = (0, 1, 2, 3)
        timeout_idx = (4, 5, 6)
        ok = (all(rows[i][3] is True for i in success_idx)
              and all(rows[i][3] is False for i in timeout_idx))
        print(" PASS: every success settles, the genuine timeout does not."
              if ok else " FAIL: settlement policy violated!")
        return 0 if ok else 1

    relay, executor, node = build_relay(args.engine, args.transport)
    params = next((p for s, p in DEMO_SCENES if s == args.skill), {})
    result = run_once(relay, executor, args.skill, params)
    if node:
        node.stop()
    print("\n" + "=" * 68)
    print(" done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
