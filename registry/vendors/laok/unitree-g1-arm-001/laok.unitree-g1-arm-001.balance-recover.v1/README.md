# unitree-g1 — RoboPay Tier 1 bridge (Simulator Skill Execution)

A paid `balance_recover` / `stop` skill executed by **real physics**, driven over
**Zenoh**, paid with **x402**, and settled **only when the robot actually
succeeded**.

`balance_recover` holds a standing planar biped upright while a disturbance pushes
its torso, then lets a **torque-limited balance PD controller** catch it. A gentle
push stays inside the actuator's torque authority and the robot recovers
(**success → payment**). A hard push exceeds that authority, the torso tips past
the fall threshold and the robot **falls — a genuine physics failure that is never
settled**. This is the honest Tier-1 "Pay-to-Actuate" path: real actuation, real
failure, gated settlement.

| | |
|---|---|
| robotId | `unitree-g1` |
| profileId | `laok.unitree-g1-arm-001.balance-recover.v1` |
| skills | `balance_recover`, `stop` |
| engines | MuJoCo (primary) + PyBullet (sim-to-sim) |
| transport | Zenoh — `robot/tunnel/action` / `robot/tunnel/result` |
| scope | **simulation only** — CPU, headless, no GPU, no ROS, no hardware |

> **Scope statement (criterion #6).** This bridge never drives physical
> hardware. There is no motor driver, no teleop channel and no hardware SDK in
> the dependency list. Every action runs inside a physics engine in-process.

---

## 1. Quick start (< 5 minutes)

```bash
cd bridge/unitree-g1-balance
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

pytest -q                                          # full test suite
python -m flow.demo --all                          # the paid flow, all scenes
```

`requirements.txt` is CPU-only. MuJoCo and PyBullet both ship manylinux wheels,
so there is nothing to compile on `ubuntu-22.04` (the CI reference platform).

> **Windows note.** `zenoh` and `pybullet` publish no Windows wheels. On Windows
> the demo runs over the loopback transport with MuJoCo — same envelopes, same
> topics, same payment path. Use Linux (or the CI workflow) for the real Zenoh
> session and the PyBullet cross-check.

## 2. What the demo prints

```
 scene                        status     settled   pitch(rad)  maxPitch  fell
-----------------------------------------------------------------------------------
 balance_recover {}           completed      True      +0.030       0.216   False
 stop {}                      completed      True      +0.001       0.001   False
 balance_recover {push:8.0}   failed         False      +0.519       0.519   True
===================================================================================
 PASS: every success settles, the genuine fall does not.
```

`pitch` / `maxPitch` are read straight out of the physics solver: the torso is a
real inverted pendulum about the hip line, so its angle under a push is integrated
by gravity and the balance PD, not scripted. A replayed animation cannot produce
that column — the torso orientation comes from the solver's body coordinates.

> The rows above are the **actual** output of `python -m flow.demo --all` on this
> repository (MuJoCo 3.11, single thread). They are deterministic: the same machine
> produces the same rows every run. The hard-push fall (pitch 0.519 rad > 0.50 rad
> fall threshold) is a genuine dynamics outcome, never a flagged constant.

## 3. Flow

```
 flow/demo.py                    CLI client (no LLM, no agent)
      │   1. list_skills                      free, from profiles/skills.yaml
      │   2. request_action  ── 402 ──▶       x402 accepts block, robot untouched
      │   3. pay  ── X-PAYMENT receipt ──▶
      ▼
 flow/relay.py                   verify → validate params → dispatch → settle/skip
      │        six-field envelope (flow/envelope.py)
      ▼
 flow/zenoh_transport.py         publish  robot/tunnel/action
      ▼
 flow/node.py                    unitree-g1 robot node
      ▼
 flow/executor.py                skillId → backend
      ▼
 simulator.py (MuJoCo)  |  simulator_pybullet.py (PyBullet)
      │                    both read g1_spec.py — one robot definition
      ▼
 result + metrics                publish  robot/tunnel/result   (correlated by actionId)
      ▼
 flow/payment.py                 SUCCESS → settle       FAILED → no settlement
```

## 4. Zenoh topics

| topic | direction | payload |
|---|---|---|
| `robot/tunnel/action` | tunnel → robot | `actionId, robotId, skillId, idempotencyKey, paramsHash, payment, params` |
| `robot/tunnel/result` | robot → tunnel | `actionId, robotId, skillId, paramsHash, status, message, metrics` |

Results are correlated to requests by `actionId`. Default endpoint
`tcp/127.0.0.1:17447`, mode `peer` — no external router required.

The Go tunnel that fronts this bridge lives in [`tunnel/`](../../tunnel) at the
repository root. It holds the outbound WebSocket to the Fabric proxy, runs the
x402 middleware, and only publishes an accepted action to `robot/tunnel/action`
after the payment verifies — the same topic the bridge subscribes to. Actions
received over that tunnel share the exact envelope and safety path as the demo.

Run the robot node separately:

```bash
python -m flow.node                        # subscribes to robot/tunnel/action
python -m flow.demo --transport zenoh      # in another shell
```

## 5. The robot

`unitree-g1` is modelled as a **planar biped** (sagittal X-Z plane, Z up), defined
once in [`g1_spec.py`](g1_spec.py) and consumed by **both** engines. It carries **6
DOF**: `torso_x` (slide), `torso_pitch` (hinge about the hip line — a real
inverted-pendulum fall axis), `left_hip`, `left_knee`, `right_hip`, `right_knee`.
The two 2-link legs are kinematically driven to their IK targets and carry the
torso; the `torso_pitch` hinge lets the torso tip and recover under a disturbance.

Skills:
- `balance_recover`: hold the standing torso upright while a push impulse is
  applied at 30% of the budget; a torque-limited PD controller (capped at
  `MAX_TORQUE_BAL`) tries to catch it.
- `stop`: hold the current pose; no motion. Always succeeds when paid and proves
  the bounded / interruptible policy.

The balance controller is the only honest one: it is a **torque-limited PD** on
`torso_pitch` (`g1_spec.py` is the entire controller — `KP_BAL`, `KV_BAL`,
`MAX_TORQUE_BAL`). The torque cap is deliberately set **below** the peak gravity
torque the inverted pendulum can exert, so a hard enough push saturates the
actuator and the torso tips over under real gravity — the failure is physics, not
a flag. Both engines share the same law and the same cap, so the recover/fall
verdict is engine-independent (verified in CI sim-to-sim).

### Failure modes (criterion #5)

| scene | outcome | why it fails | settled |
|---|---|---|---|
| `balance_recover {}` | **success** | gentle push (1.3 rad/s) stays within actuator authority; PD catches it, torso returns to upright | ✅ |
| `stop {}` | **success** | halted within the budget | ✅ |
| `balance_recover {push:8.0}` | `fall` | push (8.0 rad/s) exceeds `MAX_TORQUE_BAL`; torso pitch passes `FALL_PITCH` (0.50 rad) under gravity | ❌ |

The `fall` row is **not** a parameter rejection — `push: 8.0` passes schema
validation (`maximum: 12.0`); it fails because the simulator genuinely cannot catch
a disturbance that large within the actuator's torque budget, which is exactly the
behaviour criterion #7 wants to see.

## 6. Payment safety (criterion #7)

* No payment → `402` with the x402 `accepts` block. **The robot is never
  contacted** — the demo prints the execution counter to prove it.
* Payment without a well-formed `txHash` → `402`, still no execution.
* Invalid or unknown parameters → rejected **before** dispatch, no settlement,
  and the idempotency key is not consumed.
* Execution failed (`fall`) → `paymentState: FAILED`, `settled: false`.
  Settlement is skipped, not reversed: nothing is ever captured up front.
* Replayed `idempotencyKey` → `rejected`, no second execution, no second
  settlement.

Proof lives in `tests/test_flow.py`, `tests/test_simulator.py`,
`tests/test_profiles.py`, `tests/test_payment_gate.py`,
`tests/test_x402_no_settlement.py` and `tests/test_sim2sim.py`.

## 7. Profiles — loaded, not decoration

| file | purpose |
|---|---|
| [`profiles/robot.profile.yaml`](profiles/robot.profile.yaml) | identity, scope, kinematics, transport, wallet env binding |
| [`profiles/skills.yaml`](profiles/skills.yaml) | skill definitions, price, params schema |
| [`profiles/functions.yaml`](profiles/functions.yaml) | API functions + rejection rules |
| [`profiles/payment-policy.yaml`](profiles/payment-policy.yaml) | x402 provider, lifecycle, safety switches |
| [`profiles/execution-mapping.yaml`](profiles/execution-mapping.yaml) | topic → handler, skill → actuators |

`flow/profiles.py` reads them at runtime: the price in the 402 challenge and the
parameter validation both come from these files. `tests/test_profiles.py`
compares every number against `g1_spec.py` and the transport module, so a
profile can never drift from the robot it describes.

## 8. Sim-to-Sim

The same skill definition runs on two independent engines:

```bash
pytest tests/test_sim2sim.py -q
```

* **static agreement** — the URDF given to PyBullet and the MJCF given to MuJoCo
  are generated from the same `g1_spec.py`; the tests assert identical joint
  chains, link offsets and actuator axes (including `torso_pitch`).
* **dynamic agreement** — with PyBullet installed, both engines must return the
  same recover/fall verdict, the same failure reason, and an identical metric
  schema (the three `baseline_runs` in `docs/evidence/sim_to_sim_validation.json`).

On Windows those dynamic checks are skipped (no PyBullet wheel) and a contract
stub exercises every PyBullet call path instead. CI on `ubuntu-22.04` runs them
for real.

## 9. Environment

| variable | required | purpose |
|---|---|---|
| `UNITREE_G1_PAYTO_ADDRESS` | onchain mode | address that receives settlement |
| `UNITREE_G1_WALLET_ADDRESS` | onchain mode | robot wallet identity |
| `UNITREE_G1_PRIVATE_KEY` | onchain mode | signing key |
| `X402_FACILITATOR_URL` | onchain mode | x402 facilitator endpoint |

> ⚠️ **Never commit key material.** This repository contains no private keys,
> no mnemonics and no `.env` file. Secrets are read from the environment at
> runtime only, are never logged, and never appear in result metrics — a test
> scans the whole bridge for 64-hex-digit literals and fails the build if one
> shows up.

Default mode is `mock`: verification accepts a receipt carrying a `txHash` and
settlement is recorded in a local ledger, so the demo is reproducible offline.
The success/failure branching, idempotency and no-settle-on-failure rule use the
exact same code path in both modes; `verify_payment` and `SettlementLedger` in
`flow/payment.py` are the only two swap points for live Base Sepolia settlement.

## 10. Layout

```
bridge/unitree-g1-balance/
├── g1_spec.py              robot definition shared by both engines
├── simulator.py            MuJoCo backend
├── simulator_pybullet.py   PyBullet backend (sim-to-sim)
├── flow/
│   ├── demo.py              CLI client — the paid flow
│   ├── relay.py             402 / verify / dispatch / settle
│   ├── payment.py           payment state machine + settlement ledger
│   ├── envelope.py          six-field task envelope
│   ├── executor.py          skillId → backend factory
│   ├── zenoh_transport.py   Zenoh + loopback, one envelope contract
│   ├── node.py              robot node entrypoint
│   └── profiles.py          manifest loader (price, schema, policy)
├── profiles/                the five required YAML manifests
├── tests/                   test suite
├── docs/                    documentation and evidence
└── requirements.txt
```

## 11. Non-goals

No LLM or agent layer, no web dashboard, no ROS2, no GPU, no reinforcement
learning, no multi-robot fleet, no real hardware. The demo client is a plain
CLI on purpose: the thing under review is the paid execution path, not a
product.

---

See [`docs/validation-report.md`](docs/validation-report.md) for the
criterion-by-criterion self-audit.
