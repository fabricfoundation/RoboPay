# deep-robotics-m20-pro Tier 1 — Validation Report

## Summary
- **Robot**: deep-robotics-m20-pro, modelled as a **planar quadruped (Lite3-class)** with 8 actuated joints — 4 legs × hip/knee, plus a posture-locked torso (X/Z translation only, no rotation)
- **Tier**: 1 (Simulator Skill Execution)
- **Skills**: `move_forward`, `navigate_obstacle`, `stop`
- **Engine**: MuJoCo (primary) + PyBullet (sim-to-sim twin, import-guarded)
- **Transport**: Zenoh (real tunnel) — actions are gated on x402 verification before dispatch
- **Payment**: x402 (EIP-3009 `transferWithAuthorization`) settled on Base Sepolia

> Morphology is defined parametrically in `engine.py` (Morphology): torso 0.20 m, thigh 0.22 m, shank 0.24 m, hip_y 0.12 m, hip_x 0.18 m, walk speed 0.65 m/s.
> The controller is a deterministic IK + step-synced velocity drive (2-link leg IK per leg, diagonal-couple stepping,
> policy/state-machine triggered — **not** replay). Forward displacement is read
> from the physics solver, not from a scripted trajectory.

## Acceptance Criteria Coverage

### Criterion #1: Real Go Tunnel Integration
✅ The repository-root `tunnel/` Go binary (real RoboPay stack) verifies the
x402 payment **before** dispatch and only publishes an accepted action to
`robot/tunnel/action` after successful verification. This bridge executes that
topic via `flow/zenoh_transport.py` + `flow/relay.py`.
- Covered by `tests/test_x402.py`, `tests/test_payment_gate.py` and
  `tests/test_x402_no_settlement.py`.

### Criterion #2: Zenoh Bridge
✅ Topics: `robot/tunnel/action` (request) / `robot/tunnel/result` (result),
correlated via `actionId` (idempotency key). Real Zenoh session on Linux/macOS;
loopback transport in headless CI and on Windows (no zenoh wheel).

### Criterion #5: Failure Modes
✅ All failure paths execution-gated, **never settle on failure**:
- `timeout`: step budget exhausted → no settlement
- `collision`: leg/curb contact detected → no settlement
- `invalid params`: rejected before dispatch → no settlement
- `replay`: same idempotency key re-submitted → rejected, no re-execution, no re-settlement

### Criterion #6: Scope Classification
✅ simulator-only — no motor driver, no teleop channel, no hardware SDK.
CPU-only headless execution (`profiles/robot.profile.yaml` declares
`simulationOnly: true`).

### Criterion #7: Payment Safety (real on-chain proof)
✅ x402 payment verification:
- No payment → 402, robot untouched (execution counter stays 0)
- Invalid payment (`isValid:false` / malformed `txHash`) → 402, no execution
- Successful payment → execution → settlement
- Failed execution → no settlement

**Real settlement evidence**: `docs/evidence/x402-evidence.json` contains one
genuine Base Sepolia USDC transfer, independently verified on
[sepolia.basescan.org](https://sepolia.basescan.org/tx/0xb7253cbdb9ee952ed29d93fbee03372a9c609cd756273304f6a9f55cdbea3006):

| field | value |
|---|---|
| txHash | `0xb7253cbdb9ee952ed29d93fbee03372a9c609cd756273304f6a9f55cdbea3006` |
| block | `45636896` (confirmed by sequencer, status **Success**) |
| payer | `0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a` |
| payee | `0x742d35Cc6634C0532925a3b844Bc454e4438f44e` |
| amount | `0.1 USDC` |
| asset | `0x036CbD53842c5426634e7929541eC2318f3dCF7e` (canonical Base Sepolia USDC) |
| mechanism | EIP-3009 `transferWithAuthorization` |
| resource | `robopay://deep-robotics-m20-pro/move_forward` |

The transaction was verified live against Base Sepolia on 2026-08-18: status
Success, the `Transfer` event moves exactly 0.1 USDC from the payer to the
payee. No private key is stored in this repository.
