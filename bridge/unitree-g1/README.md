# unitree-g1 — RoboPay Tier 1 bridge (Simulator Skill Execution)

A paid `pick_and_carry` / `stop` skill executed by **real physics**, driven over
**Zenoh**, paid with **x402**, and settled **only when the robot actually
succeeded**. This is a **humanoid pick-and-carry** task (Tier 1, B1), built on
the same G1 bridge that previously carried a plain-walk skill — deliberately
kept on a distinct `pick-and-carry.v1` profile so it does **not** collide with
the `#24` obstacle-avoidance track or the old `#90` walk track.

| | |
|---|---|
| robotId | `unitree-g1` |
| profileId | `laok.unitree-g1-arm-001.pick-and-carry.v1` |
| skills | `pick_and_carry`, `stop` |
| engines | MuJoCo (primary) + PyBullet (sim-to-sim) |
| transport | Zenoh — `robot/tunnel/action` / `robot/tunnel/result` |
| scope | **simulation only** — CPU, headless, no GPU, no ROS, no hardware |

> **Scope statement (criterion #6).** This bridge never drives physical
> hardware. There is no motor driver, no teleop channel and no hardware SDK in
> the dependency list. Every action runs inside a physics engine in-process.

> **Track separation.** `pick_and_carry` is the only locomotion/actuation skill
> here. The `#24` `navigate_obstacle` (curb-crossing) and the `#90` plain
> `move_forward` walk skills live on different profiles; this PR adds a new
> pick-and-carry capability without touching them.

---

## 1. Quick start (< 5 minutes)

```bash
cd bridge/unitree-g1
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
 skill             status      settled   dist(m)   steps
------------------------------------------------------------------------------
 pick_and_carry    completed      True    2.0002     957
 stop              completed      True    0.0002      50
 pick_and_carry {'dropDistance': 8.0}failed        False    2.0884    1000
==============================================================================
 PASS: every success settles, the genuine timeout does not.
```

`dist` and `steps` are read out of the physics engine: the robot is a planar
biped whose forward displacement comes from real MuJoCo friction contacts
between the planted foot and the ground, plus a 2-link inverse-kinematics swing
foot. A replayed animation cannot produce that column — the torso position is
taken straight from the solver's body coordinates. The object is modelled as
co-located with the torso (a box the biped carries), so `carried` flips to
`True` once the pickup zone is passed and stays set through the carry.

> The numbers above are the **actual** output of `python -m flow.demo --all`
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
once in [`g1_spec.py`](g1_spec.py) and consumed by **both** engines. It carries
**4 actuated joints** — `left_hip`, `left_knee`, `right_hip`, `right_knee` — all
hinge joints in the sagittal plane. The torso is posture-locked: it has only X
(forward) and Z (vertical) translation DOF, never a rotation, so the robot is
deterministically upright.

Skills:
- `pick_and_carry`: walk forward to a **pickup zone** (`pickupDistance`, default
  1.0 m), acquire the carried object (modelled as co-located with the torso on
  this planar biped), then **carry** it to a **drop zone** (`dropDistance`,
  default 2.0 m). Success when the torso reaches the drop zone within the step
  budget after passing the pickup zone.
- `stop`: bring the biped to rest and hold both feet planted — the safe-stop
  primitive.

Locomotion is produced the only honest way: two 2-link legs step in a fixed,
deterministic gait, the planted foot anchors to the ground through real MuJoCo
friction contacts, and the torso is carried forward by the leg geometry. There is
**no learned policy and no potential field** — `g1_spec.py` is the entire
controller, and it is pure 2-link inverse kinematics plus a step-synced velocity
drive. Nothing about the trajectory is scripted: the forward displacement and the
carry state are read straight out of the physics engine's solved body positions.

### Failure modes (criterion #5)

| scene | outcome | why it fails | settled |
|---|---|---|---|
| `pick_and_carry` | **success** | walked to pickup zone, acquired object, reached drop zone (2.0002 m) | ✅ |
| `stop` | **success** | halted within the budget | ✅ |
| `pick_and_carry {'dropDistance': 8.0}` | `timeout` | a drop distance of 8.0 m is valid per schema (`maximum: 8.0`) but larger than any gait budget can reach (~2.2 m), so the real physics runs the full step budget and exhausts it | ❌ |

The `timeout` row is **not** a parameter rejection — `dropDistance: 8.0` passes
schema validation; it fails because the simulator genuinely cannot carry that
far within the step budget, which is the behaviour criterion #7 wants to see.

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
compares every number against `g1_spec.py` and the transport module, so a
profile can never drift from the robot it describes.

## 8. Sim-to-Sim

The same skill definition runs on two independent engines:

```bash
pytest tests/test_sim2sim.py -q
```

* **static agreement** — the URDF given to PyBullet and the MJCF given to MuJoCo
  are generated from the same `g1_spec.py`; the tests assert identical joint
  chains, link offsets and actuator axes.
* **dynamic agreement** — with PyBullet installed, both engines must return the
  same verdict, the same failure reason, and an identical metric schema
  (`reached`, `pickupReached`, `carried`, `objectX`, `pickupX`).

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
bridge/unitree-g1/
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
