# Unitree G1 Tier 1 — Validation Report

## Summary
- **Robot**: Unitree G1, modelled as a **planar biped** (sagittal X-Z plane) with **4 actuated joints** — `left_hip`, `left_knee`, `right_hip`, `right_knee` — plus a posture-locked torso (X/Z translation only, no rotation)
- **Tier**: 1 (Simulator Skill Execution)
- **Skills**: `pick_and_carry`, `stop` (Tier 1 B1 humanoid pick-and-carry)
- **Engine**: MuJoCo (primary) + PyBullet (sim-to-sim)
- **Transport**: Zenoh (real tunnel) — `tunnel/` at the repo root hosts the Go tunnel binary; actions are gated on x402 verification before dispatch
- **Payment**: x402 (EIP-3009 `transferWithAuthorization`) settled through the public x402 facilitator on Base Sepolia

> Embodiment note: `29-DOF humanoid` and any "learned / potential-field policy"
> description are **wrong** for this submission and were removed. The robot is a
> deterministic planar biped whose entire controller is `g1_spec.py` (2-link IK
> + step-synced velocity drive). The forward displacement is read from the
> physics solver, not from a replay.

## Acceptance Criteria Coverage

### Criterion #1: Real Go Tunnel Integration Test
✅ `tunnel/` (repository root) is the real Go tunnel binary from the RoboPay
stack. It verifies the x402 payment **before** dispatch and only publishes an
accepted action to `robot/tunnel/action` after successful verification.
- The G1 bridge subscribes to that same Zenoh topic (`flow/zenoh_transport.py`)
  and executes the action via `flow/relay.py`.
- Covered by `tests/test_bridge.py` (the 402 challenge is shaped exactly like the
  published payment policy) and `tests/test_x402.py` / `tests/test_x402_no_settlement.py`.

### Criterion #2: Zenoh Bridge
✅ Topics: `robot/tunnel/action` (request) / `robot/tunnel/result` (result).
- Correlation via `actionId` (idempotency key).
- Real Zenoh session on Linux/macOS; loopback transport used in headless CI and on Windows (no zenoh wheel).

### Criterion #5: Failure Modes
✅ All failure paths tested (execution-gated, never settle on failure):
- `timeout`: step budget exhausted before the drop zone was reached → no settlement
- `invalid params`: rejected before dispatch → no settlement
- `replay`: same idempotency key re-submitted → rejected, no re-execution, no re-settlement
- `stop` (safe-stop): a bounded, interruptible primitive — the run always terminates
  cleanly and never leaves the robot mid-gait

### Criterion #6: Scope Classification
✅ simulator-only
- No motor driver, no teleop channel, no hardware SDK
- CPU-only, headless execution (`profiles/robot.profile.yaml` declares `simulationOnly: true`)

### Criterion #7: Payment Safety (real on-chain proof)
✅ x402 payment verification
- No payment → 402, robot untouched (execution counter stays 0)
- Invalid payment (`isValid:false` / malformed `txHash`) → 402, no execution
- Successful payment → execution → settlement
- Failed execution → no settlement

**Real settlement evidence**: `docs/evidence/x402-evidence.json` contains one genuine Base Sepolia USDC transfer, independently verified on
[sepolia.basescan.org](https://sepolia.basescan.org/tx/0xcb9cab548125ddf34980bf14a5bbb57d8a86d9896348a46d63f9178f34470cc4):

| field | value |
|---|---|
| txHash | `0xcb9cab548125ddf34980bf14a5bbb57d8a86d9896348a46d63f9178f34470cc4` |
| block | `45415117` (confirmed by sequencer, status **Success**) |
| payer | `0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a` |
| payee | `0x742d35Cc6634C0532925a3b844Bc454e4438f44e` |
| amount | `0.1 USDC` |
| asset | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (canonical Base Sepolia USDC) |
| mechanism | EIP-3009 `transferWithAuthorization` (the on-chain `AuthorizationUsed` event is present) |
| resource | `robopay://unitree-g1-arm-001/pick_and_carry` |

The transaction was verified live against Base Sepolia on 2026-08-13: status
Success, block 45415117, the `Transfer` event moves exactly 0.1 USDC from the
payer to the payee, and the `AuthorizationUsed` event confirms EIP-3009. No
private key is stored in this repository; the payer key lives off-repo.

### Criterion #8: Robot Identity & Wallet Binding
✅ Envelope binds `robotId` to the settlement receipt.
- `UNITREE_G1_WALLET_ADDRESS` (payee) supplied via environment; no private keys in repository.
- The payer key is held off-repo and only used to broadcast the settlement; it is never committed.

## Deterministic-Gait Controller (not a policy)
The locomotion is **entirely in `g1_spec.py`**: two 2-link legs run a fixed,
deterministic stepping gait; the planted foot is anchored to the ground through
real MuJoCo friction contacts; the swing foot is placed ahead by a 2-link
inverse-kinematics solver. There is no potential field, no reinforcement
learning, and no runtime policy — so every run is reproducible in CI.

## Sim-to-Sim Validation
- Same skill definition runs on both MuJoCo and PyBullet
- Dynamic agreement: same verdict, same metrics (`tests/test_sim2sim.py`)
- Static agreement: identical joint chains, link offsets (`tests/test_profiles.py`)

## Evidence (all real)
- `docs/evidence/x402-evidence.json`: **1 real on-chain settlement** (Base Sepolia USDC Transfer, independently verifiable on basescan)
- `docs/evidence/settle.png`: rendered from the real terminal run (`docs/evidence/terminal/output.txt`)
- `docs/evidence/terminal/output.txt`: full 402→pay→simulate→settle→replay-rejected log
- `docs/evidence/evidence-manifest.yaml`: sha256 + size of every evidence artifact

---

*Generated: 2026-08-13 · settlement verified on Base Sepolia block 45415117*

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
  the real terminal run (payer `0xF274…`, txHash `0xcb9ca…`, block
  `45415117`).
