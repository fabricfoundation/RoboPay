# unitree-g1 — RoboPay Tier 1 bridge (Simulator Skill Execution)

A paid `pick_and_carry` / `stop` skill executed by **real physics**, driven over
**Zenoh**, paid with **x402**, and settled **only when the robot actually
succeeded**. This is the registry package for the **humanoid pick-and-carry**
Tier 1 task (B1) — a distinct `pick-and-carry.v1` profile that does not overlap
the `#24` obstacle-avoidance track or the old `#90` walk track.

| | |
|---|---|
| robotId | `unitree-g1` |
| profileId | `laok.unitree-g1-arm-001.pick-and-carry.v1` |
| skills | `pick_and_carry` / `stop` — 0.10 USDC each, Base Sepolia |
| engines | MuJoCo (primary) + PyBullet (sim-to-sim) |
| transport | Zenoh — `robot/tunnel/action` / `robot/tunnel/result` |
| scope | **simulation only** — CPU, headless, no GPU, no ROS, no hardware |

> **Scope statement (criterion #6).** This bridge never drives physical
> hardware. There is no motor driver, no teleop channel and no hardware SDK in
> the dependency list. Every action runs inside a physics engine in-process.

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

`dist` and `steps` are read out of the physics engine: the torso is a free
rigid body with mass and ground friction, and it only moves because the
deterministic 2-link IK stepping gait pushes against real contacts. A replayed
animation cannot produce that column.

## 3. Skills

| skillId | displayName | params | failure modes |
|---|---|---|---|
| `pick_and_carry` | Pick and carry an object | `pickupDistance` (0.1–6.0 m, default 1.0), `dropDistance` (0.2–8.0 m, default 2.0), `speed` (0–1.5) | `timeout` (step budget exhausted) |
| `stop` | Safe stop | none | — |

Pricing is declared in `skills.yaml` and served verbatim by the HTTP 402
challenge (`X-PAYMENT` / `PAYMENT-REQUIRED`). Settlement is
**on-success-only**; failure / timeout / replay paths never settle.

## 4. Payment (x402, Base Sepolia USDC)

- Network: `eip155:84532` (Base Sepolia)
- Asset: `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (canonical USDC)
- Amount: `0.10` USDC per execution
- Settlement: EIP-3009 `transferWithAuthorization`, only after a correlated
  simulator success result
- Real on-chain proof: `docs/evidence/x402-evidence.json` (tx
  `0xcb9cab54…34470cc4`, block `45415117`, payer `0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a`,
  payee `0x742d35Cc6634C0532925a3b844Bc454e4438f44e`)
- Secrets: none committed; keys are read from environment variables only

## 5. Structure

```
registry/vendors/laok/unitree-g1-arm-001/laok.unitree-g1-arm-001.pick-and-carry.v1/
├── robot.profile.yaml          # robot identity, scope, wallet binding (env-only)
├── skills.yaml                 # skill catalogue + pricing (loaded at runtime)
├── functions.yaml              # HTTP-facing function manifest
├── payment-policy.yaml         # payment safety flags (all false where forbidden)
├── execution-mapping.yaml      # scene table, budgets, decision thresholds
├── skill-catalog.json          # tunnel-side machine-readable catalogue
├── examples/                   # sample action envelopes
├── docs/                       # validation report, demo script, evidence
└── tests/                      # registry-contract tests
```

The full implementation lives in `bridge/unitree-g1/` at the repository root.
`docs/validation-report.md` maps every artifact to the 7 acceptance criteria.
`docs/task-traceability.md` and `docs/field-validation-runbook.md` give the
test-to-criterion mapping and a step-by-step reviewer reproduction guide.
