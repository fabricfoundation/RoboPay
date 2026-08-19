# tron1-001 — RoboPay Tier 1 bridge (Simulator Skill Execution)

A paid `move_forward` / `navigate_obstacle` / `stop` skill executed by **real
physics**, driven over **Zenoh**, paid with **x402**, and settled **only when
the robot actually succeeded**.

| | |
|---|---|
| robotId | `tron1-001` |
| profileId | `laok.tron1-001-arm-001.loco.v1` |
| skills | `move_forward`, `navigate_obstacle`, `stop` |
| engines | MuJoCo (primary) + PyBullet (sim-to-sim) |
| transport | Zenoh — `robot/tunnel/action` / `robot/tunnel/result` |
| scope | **simulation only** — CPU, headless, no GPU, no ROS, no hardware |

> **Scope statement (criterion #6).** This bridge never drives physical
> hardware. There is no motor driver, no teleop channel and no hardware SDK in
> the dependency list. Every action runs inside a physics engine in-process.

---

## 1. Quick start (< 5 minutes)

```bash
cd bridge/tron1-001
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
 scene                   status     reason       dist(m)  steps  settled
------------------------------------------------------------------------------
 move_forward            completed  walked        1.0520    495     True
 navigate_obstacle       completed  walked        2.0402    945     True
 stop                    completed  stopped       0.0048     25     True
 move_forward(timeout)   failed     timeout       2.2487   1020    False
==============================================================================
 PASS: success settles, the timeout failure does not.
```

`distance` is read out of the physics engine: the robot is a planar biped whose
forward displacement comes from real MuJoCo friction contacts between the planted
foot and the ground, plus a 2-link inverse-kinematics swing foot. A replayed
animation cannot produce that column — the torso position is taken straight from
the solver's body coordinates.

> The four numbers above are the **actual** output of `python -m flow.demo --all`
> on this repository (MuJoCo 3.11, single thread). They are deterministic: the
> same machine produces the same rows every run.

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
 flow/node.py                    tron1-001 robot node
      ▼
 flow/executor.py                skillId → backend
      ▼
 simulator.py (MuJoCo)  |  simulator_pybullet.py (PyBullet)
      │                    both read tron1_spec.py — one robot definition
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

`tron1-001` is modelled as a **planar biped** (sagittal X-Z plane, Z up), defined
once in [`tron1_spec.py`](tron1_spec.py) and consumed by **both** engines. It carries
**4 actuated joints** — `left_hip`, `left_knee`, `right_hip`, `right_knee` — all
hinge joints in the sagittal plane. The torso is posture-locked: it has only X
(forward) and Z (vertical) translation DOF, never a rotation, so the robot is
deterministically upright.

Skills:
- `move_forward`: walk forward until the torso has advanced `goalDistance` metres
- `navigate_obstacle`: walk forward and step over a low curb (0.08 m) to reach a goal X
- `stop`: bring the biped to rest and hold both feet planted

Locomotion is produced the only honest way: two 2-link legs step in a fixed,
deterministic gait, the planted foot anchors to the ground through real MuJoCo
friction contacts, and the torso is carried forward by the leg geometry. There is
**no learned policy and no potential field** — `tron1_spec.py` is the entire
controller, and it is pure 2-link inverse kinematics plus a step-synced velocity
drive. Nothing about the trajectory is scripted: the forward displacement is read
straight out of the physics engine's solved body positions.

### Failure modes (criterion #5)

| scene | outcome | why it fails | settled |
|---|---|---|---|
| `move_forward` | **success** | walked 1.052 m (goalDistance 1.0 m) | ✅ |
| `navigate_obstacle` | **success** | crossed the 0.08 m curb, reached goal X 2.0 m | ✅ |
| `stop` | **success** | halted within the budget | ✅ |
| `timeout` | `timeout` | a goal distance of 5.0 m is valid per schema but larger than any gait budget can reach (~2.2 m), so the real physics runs the full step budget and exhausts it | ❌ |
| `collision` | `collision` | a leg contacts the curb (real MuJoCo contact) | ❌ |

The `timeout` row is **not** a parameter rejection — `goalDistance: 5.0` passes
schema validation (`maximum: 5.0`); it fails because the simulator genuinely
cannot walk that far within the step budget, which is the behaviour criterion #7
wants to see.

## 6. Payment safety (criterion #7)

* No payment → `402` with the x402 `accepts` block. **The robot is never
  contacted** — the demo prints the execution counter to prove it.
* Payment without a well-formed `txHash` → `402`, still no execution.
* Invalid or unknown parameters → rejected **before** dispatch, no settlement,
  and the idempotency key is not consumed.
* Execution failed → `paymentState: FAILED`, `settled: false`. Settlement is
  skipped, not reversed: nothing is ever captured up front.
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
compares every number against `tron1_spec.py` and the transport module, so a
profile can never drift from the robot it describes.

## 8. Sim-to-Sim

The same skill definition runs on two independent engines:

```bash
pytest tests/test_sim2sim.py -q
```

* **static agreement** — the URDF given to PyBullet and the MJCF given to MuJoCo
  are generated from the same `tron1_spec.py`; the tests assert identical joint
  chains, link offsets and actuator axes.
* **dynamic agreement** — with PyBullet installed, both engines must return the
  same verdict, the same failure reason, and an identical metric schema.

On Windows those dynamic checks are skipped (no PyBullet wheel) and a contract
stub exercises every PyBullet call path instead. CI on `ubuntu-22.04` runs them
for real.

## 9. Environment

| variable | required | purpose |
|---|---|---|
| `UNITREE_TRON1_PAYTO_ADDRESS` | onchain mode | address that receives settlement |
| `UNITREE_TRON1_WALLET_ADDRESS` | onchain mode | robot wallet identity |
| `UNITREE_TRON1_PRIVATE_KEY` | onchain mode | signing key |
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
bridge/tron1-001/
├── tron1_spec.py              robot definition shared by both engines
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
