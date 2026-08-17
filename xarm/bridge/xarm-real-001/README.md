# xarm-real-001 — RoboPay Tier 1 bridge (Simulator Skill Execution)

A paid `pick_object` skill executed by **real physics**, driven over **Zenoh**,
paid with **x402**, and settled **only when the robot actually succeeded**.

| | |
|---|---|
| robotId | `xarm-real-001` |
| profileId | `xarm.xarm-real-001.mujoco-sim.v1` |
| skill | `pick_object` — 0.10 USDC / execution, Base Sepolia |
| engines | MuJoCo (primary) + PyBullet (sim-to-sim) |
| transport | Zenoh — `robot/tunnel/action` / `robot/tunnel/result` |
| scope | **simulation only** — CPU, headless, no GPU, no ROS, no hardware |

> **Scope statement (criterion #6).** This bridge never drives physical
> hardware. There is no motor driver, no teleop channel and no hardware SDK in
> the dependency list. Every action runs inside a physics engine in-process.

---

## 1. Quick start (< 5 minutes)

```bash
cd bridge/xarm-real-001
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

pytest -q                                          # full test suite
python -m flow.demo --all                          # the paid flow, all 4 scenes
```

`requirements.txt` is CPU-only. MuJoCo and PyBullet both ship manylinux wheels,
so there is nothing to compile on `ubuntu-22.04` (the CI reference platform).

> **Windows note.** `zenoh` and `pybullet` publish no Windows wheels. On Windows
> the demo runs over the loopback transport with MuJoCo — same envelopes, same
> topics, same payment path. Use Linux (or the CI workflow) for the real Zenoh
> session and the PyBullet cross-check.

## 2. What the demo prints

```
 scene        status     reason        lifted(m)  force(N)  steps  settled
------------------------------------------------------------------------------
 cube         completed  picked           0.1313      9.81    260     True
 unreachable  failed     unreachable     -0.0002      0.00     70    False
 collision    failed     collision       -0.0002      0.00     24    False
 timeout      failed     timeout         -0.0002      0.00     60    False
==============================================================================
 PASS: success settles, every failure does not.
```

`lifted` and `force` are read out of the physics engine: the cube is a free
rigid body with mass and friction, and it only leaves the table because two
finger pads measured a normal force against it first. A replayed animation
cannot produce that column.

Single scene with the full 10-step trace:

```bash
python -m flow.demo --object cube          # success -> settlement
python -m flow.demo --object collision     # obstacle hit -> NO settlement
python -m flow.demo --engine pybullet      # same skill, second engine
python -m flow.demo --transport zenoh      # real Zenoh session (Linux/macOS)
```

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
 flow/node.py                    xarm-real-001 robot node
      ▼
 flow/executor.py                skillId → backend
      ▼
 simulator.py (MuJoCo)  |  simulator_pybullet.py (PyBullet)
      │                    both read arm_spec.py — one robot definition
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

Run the robot node separately:

```bash
python -m flow.node                        # subscribes to robot/tunnel/action
python -m flow.demo --transport zenoh      # in another shell
```

## 5. The robot

4-DoF arm (`pan`, `shoulder`, `elbow`, `wristp`) + parallel-jaw gripper,
defined once in [`arm_spec.py`](arm_spec.py) and consumed by **both** engines.

`pick_object` runs five stages — `MOVE_ABOVE → DESCEND → GRIP → LIFT → SETTLE`
— using a **deterministic trajectory controller**: the keyframes are solved in
closed form at import time, then interpolated. No runtime IK, no PD tuning, no
learned policy, therefore no machine-dependent behaviour.

What stays fully dynamic: gravity, collisions, friction, contact normal forces,
and the cube itself. The arm is a boundary condition applied to a real physics
scene; the object's motion is computed by the engine, not scripted.

The grasp is **contact-gated**: the constraint that holds the cube is only
created after both finger pads report a non-zero measured normal force.

### Failure modes (criterion #5)

| `params.object` | outcome | why it fails | settled |
|---|---|---|---|
| `cube` | **success** | lifted 0.131 m at 9.8 N | ✅ |
| `unreachable` | `unreachable` | cube at 0.95 m, arm reach 0.52 m — the arm stretches and stops short | ❌ |
| `collision` | `collision` | obstacle pillar on the approach path is contacted | ❌ |
| `timeout` | `timeout` | step budget clipped to 60 (nominal 260) | ❌ |
| any, weak grip | `grasp_failed` | contact force or lift below threshold | ❌ |

Thresholds: `contactForce ≥ 0.30 N`, `objectLifted ≥ 0.030 m`,
`graspState == attached` — all three must hold (`arm_spec.py`).

## 6. Payment safety (criterion #7)

* No payment → `402` with the x402 `accepts` block. **The robot is never
  contacted** — the demo prints the execution counter to prove it.
* Payment without `txHash` → `402`, still no execution.
* Invalid or unknown parameters → rejected **before** dispatch, no settlement,
  and the idempotency key is not consumed.
* Execution failed → `paymentState: FAILED`, `settled: false`. Settlement is
  skipped, not reversed: nothing is ever captured up front.
* Replayed `idempotencyKey` → `rejected`, no second execution, no second
  settlement.

Proof lives in `tests/test_flow.py`, `tests/test_simulator.py`,
`tests/test_profiles.py` and `tests/test_sim2sim.py`; the policy file lists the
exact test IDs and `tests/test_profiles.py` asserts those tests exist.

## 7. Profiles — loaded, not decoration

| file | purpose |
|---|---|
| [`profiles/robot.profile.yaml`](profiles/robot.profile.yaml) | identity, scope, kinematics, transport, wallet env binding |
| [`profiles/skills.yaml`](profiles/skills.yaml) | `pick_object` price, params schema, success criteria, failure modes |
| [`profiles/functions.yaml`](profiles/functions.yaml) | `list_skills` / `request_action` / `submit_paid_action` + rejection rules |
| [`profiles/payment-policy.yaml`](profiles/payment-policy.yaml) | x402 provider, lifecycle, safety switches, secret handling |
| [`profiles/execution-mapping.yaml`](profiles/execution-mapping.yaml) | topic → handler, skill → keyframes/stages, scene table |

`flow/profiles.py` reads them at runtime: the price in the 402 challenge and the
parameter validation both come from these files. `tests/test_profiles.py` (38
tests) compares every number against `arm_spec.py` and the transport module, so
a profile can never drift from the robot it describes.

## 8. Sim-to-Sim

The same `pick_object` definition runs on two independent engines:

```bash
pytest tests/test_sim2sim.py -q
```

* **static agreement** — the URDF given to PyBullet and the MJCF given to MuJoCo
  are generated from the same `arm_spec.py`; the tests assert identical joint
  chains, link offsets and gripper axes.
* **dynamic agreement** — with PyBullet installed, both engines must return the
  same verdict, the same failure reason, the same grasp state, lift heights
  within 0.03 m, and an identical metric schema.

On Windows those dynamic checks are skipped (no PyBullet wheel) and a contract
stub exercises every PyBullet call path instead. CI on `ubuntu-22.04` runs them
for real.

## 9. Environment

| variable | required | purpose |
|---|---|---|
| `FABRIC_ARM_PAYTO_ADDRESS` | onchain mode | address that receives settlement |
| `FABRIC_ARM_WALLET_ADDRESS` | onchain mode | robot wallet identity |
| `FABRIC_ARM_PRIVATE_KEY` | onchain mode | signing key |
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
bridge/xarm-real-001/
├── arm_spec.py              robot definition shared by both engines
├── simulator.py             MuJoCo backend
├── simulator_pybullet.py    PyBullet backend (sim-to-sim)
├── flow/
│   ├── demo.py              CLI client — the 10-step paid flow
│   ├── relay.py             402 / verify / dispatch / settle
│   ├── payment.py           payment state machine + settlement ledger
│   ├── envelope.py          six-field task envelope
│   ├── executor.py          skillId → backend factory
│   ├── zenoh_transport.py   Zenoh + loopback, one envelope contract
│   ├── node.py              robot node entrypoint
│   └── profiles.py          manifest loader (price, schema, policy)
├── profiles/                the five required YAML manifests
├── tests/                   76 tests (67 run on Windows, all 76 on Linux CI)
├── experiments/             superseded prototypes, not shipped
├── VALIDATION.md            13 acceptance criteria, one by one
└── requirements.txt
```

## 11. Non-goals

No LLM or agent layer, no web dashboard, no ROS2, no GPU, no reinforcement
learning, no multi-robot fleet, no real hardware. The demo client is a plain
CLI on purpose: the thing under review is the paid execution path, not a
product.

---

See [`VALIDATION.md`](VALIDATION.md) for the criterion-by-criterion self-audit.
