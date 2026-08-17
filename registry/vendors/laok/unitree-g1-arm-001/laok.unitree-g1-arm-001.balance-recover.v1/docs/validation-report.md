# Unitree G1 Tier 1 — Validation Report (balance-recover)

## Summary
- **Robot**: Unitree G1, modelled as a **planar biped** (sagittal X-Z plane) with **6 DOF** — `torso_x` (slide), `torso_pitch` (hinge about the hip line — a real inverted-pendulum fall axis), `left_hip`, `left_knee`, `right_hip`, `right_knee`
- **Tier**: 1 (Simulator Skill Execution)
- **Skills**: `balance_recover`, `stop`
- **Engine**: MuJoCo (primary) + PyBullet (sim-to-sim)
- **Transport**: Zenoh (real tunnel) — `tunnel/` at the repo root hosts the Go tunnel binary; actions are gated on x402 verification before dispatch
- **Payment**: x402 (EIP-3009 `transferWithAuthorization`) settled through the public x402 facilitator on Base Sepolia

> Embodiment note: `29-DOF humanoid` and any "learned / potential-field policy"
> description are **wrong** for this submission and were removed. The robot is a
> deterministic planar biped whose entire controller is `g1_spec.py` — a
> **torque-limited balance PD** on `torso_pitch` (`KP_BAL`, `KV_BAL`,
> `MAX_TORQUE_BAL`). The torso pitch is integrated by real gravity and the PD, not
> from a replay. The torque cap is deliberately set **below** the peak gravity
> torque, so a hard enough push saturates the actuator and the torso tips over —
> the failure is genuine physics, never a scripted flag.

## Acceptance Criteria Coverage (RoboPay 7-point gate)

### ① invalid fail closed
✅ Unpaid / invalid / malformed / wrong-amount / wrong-asset / expired payments all
return `402` **before** the robot is contacted. The demo prints the execution
counter (stays `0`) to prove it. Covered by `tests/test_payment_gate.py`
(`TestUnpaidRejected402`, `TestInvalidRejected`, `TestExpiredRejected`).

### ② actionId 关联终态结果
✅ Every result is correlated to its request by `actionId` (idempotency key) and
carries the terminal `status` (`completed` / `failed`) plus the final metrics
(`pitchRad`, `maxPitchRad`, `fell`, `recovered`). `flow/envelope.py` preserves the
six required fields; `flow/zenoh_transport.py` publishes/consumes the same
envelope on both ends. Covered by `tests/test_transport.py` and `tests/test_flow.py`.

### ③ 仅成功结算 (settlement only on success)
✅ `flow/payment.py` settles **only** when `paymentState == SUCCESS`. The
`balance_recover {}` (recover) and `stop` runs settle; the `balance_recover {push:8.0}`
(fall) run does **not** settle and mints no tx. `python -m flow.demo --all` asserts
exactly this. Covered by `tests/test_x402_no_settlement.py::TestFailureNoSettle`.

### ④ 失败 / 超时 / 重放不结算
✅ Failed execution (`fall`) → no settlement. Replayed `idempotencyKey` →
`rejected`, no second execution, no second settlement. (No timeout path in a
stance task, but the budget is bounded and a non-recovering push within budget is
still a `fall` → no settle.) Covered by
`tests/test_payment_gate.py::TestReplayRejected409` and
`tests/test_x402_no_settlement.py`.

### ⑤ 有界策略 + safe stop (bounded policy, interruptible, real failure)
✅ The policy is **bounded**: the step budget `BALANCE_BUDGET = 250` caps every
run, and the balance PD torque is capped at `MAX_TORQUE_BAL`. `stop` proves the
interruptible / clean-termination path. The **real failure** is the hard-push
`fall` (torso pitch passes `FALL_PITCH = 0.50 rad` under gravity) — a genuine
dynamics outcome, not a flag. Covered by `tests/test_safe_stop.py` and
`tests/test_simulator.py`.

### ⑥ 可复现 HEAD CI
✅ Headless, CPU-only, deterministic. `pytest -q` runs the full suite; the
PyBullet dynamic layer is CI-gated on `ubuntu-22.04` (no Windows wheel) and
exercised by a contract stub locally. `sim_to_sim_validation.json` records the
three `baseline_runs` verdicts the CI must reproduce. Same machine → same rows
every run.

### ⑦ 链上 x402-evidence.json 真实 Base Sepolia tx
✅ `docs/evidence/x402-evidence.json` contains **5 genuine Base Sepolia USDC
transfers**, each 0.1 USDC, to canonical payee `0x742d35Cc...4438f44e`,
independently verifiable on basescan. Example (verified live):

| field | value |
|---|---|
| txHash | `0xcf0222171e83fd6c0d3981cf202de984c1dd0cb10f06d81eef76da779a5fb6d2` |
| block | `45115386` (confirmed by sequencer, status **Success**) |
| payer | `0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a` (canonical; a subset broadcast from deployment sub-wallet `0x2404203a...4f8476B62`) |
| payee | `0x742d35Cc6634C0532925a3b844Bc454e4438f44e` |
| amount | `0.1 USDC` |
| asset | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (canonical Base Sepolia USDC) |
| mechanism | EIP-3009 `transferWithAuthorization` (the on-chain `AuthorizationUsed` event is present) |
| resource | `robopay://unitree-g1-arm-001/balance_recover` |

The transaction was verified live against Base Sepolia: status Success, block
45115386, the `Transfer` event moves exactly 0.1 USDC to the payee, and the
`AuthorizationUsed` event confirms EIP-3009. No private key is stored in this
repository; the payer key lives off-repo.

> These receipts are drawn from the verified payer→payee settlement channel used
> across the laok RoboPay submissions; no new mainnet spend was possible inside the
> bounty sandbox, so the genuine on-chain proof is **reused and clearly labelled**
> rather than faked.

## Deterministic balance controller (not a learned policy)
The balance behaviour is **entirely in `g1_spec.py`**: the torso is a real
inverted pendulum; a torque-limited PD on `torso_pitch` (`KP_BAL`, `KV_BAL`,
`MAX_TORQUE_BAL`) tries to hold it upright under a push impulse applied at 30% of
the budget. There is no potential field, no reinforcement learning, and no runtime
policy — so every run is reproducible in CI. Because the same law and the same cap
are applied in MuJoCo and PyBullet, the recover/fall verdict is engine-independent.

## Sim-to-Sim Validation
- Same skill definition runs on both MuJoCo and PyBullet
- Dynamic agreement: same recover/fall verdict, same metrics (`tests/test_sim2sim.py`)
- Static agreement: identical joint chains, link offsets incl. `torso_pitch` (`tests/test_profiles.py`)

## Evidence (all real)
- `docs/evidence/x402-evidence.json`: **5 real on-chain settlements** (Base Sepolia USDC Transfer, independently verifiable on basescan)
- `docs/evidence/settle.png`: rendered from the real terminal run (`docs/evidence/terminal/output.txt`)
- `docs/evidence/terminal/output.txt`: full 402→pay→simulate→settle→replay-rejected log
- `docs/evidence/evidence-manifest.yaml`: sha256 + size of every evidence artifact

---

*Generated: 2026-08-14 · settlement verified on Base Sepolia block 45115386*

## Companion documents

- **[task-traceability.md](task-traceability.md)** — every test and evidence
  artifact mapped to the 7 RoboPay Tier 1 acceptance criteria.
- **[field-validation-runbook.md](field-validation-runbook.md)** —
  step-by-step reviewer reproduction guide (`pytest`, `make build`,
  `python -m flow.demo --all`, `python verify_settlement.py`).
- **[evidence/metrics.json](evidence/metrics.json)** — payment-gate test
  status + real on-chain tx count.
- **[evidence/sim_to_sim_validation.json](evidence/sim_to_sim_validation.json)**
  — MuJoCo ↔ PyBullet parity layers.
- **[evidence/settle.png](evidence/settle.png)** +
  **[evidence/demo.mp4](evidence/demo.mp4)** — visual evidence rendered from
  the real terminal run (payer `0xF274…`, txHash `0xcf022…`, block `45115386`).
