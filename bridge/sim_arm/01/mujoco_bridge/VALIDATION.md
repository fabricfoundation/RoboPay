# Validation — sim-arm-01 Tier 1

**Robot:** `sim-arm-01` — 2-DOF planar arm
**Skill:** `move_to_pose` (priced `0.50` USDC)
**Scope:** simulator-only
**Simulators:** MuJoCo 3.10.0 (primary), PyBullet 3.2.7 (sim-to-sim)

Everything below is reproducible with `mujoco`, `pybullet`, `numpy`, `pytest` —
no ROS2 or live Zenoh router required. Run from this package directory
(`bridge/sim_arm/01/mujoco_bridge/`).

---

## How this addresses the review

| Review blocker | Resolution | Where |
|---|---|---|
| Runtime started *after* payment already settled | Settlement is now driven by the terminal result, not by submission. `submit()` returns `accepted`/pending and does **not** settle; the relay settles only when it consumes a `success` result. | `sim_arm_01/flow/relay.py`, `sim_arm_01/flow/payment.py` |
| Only subscribed to `robot/tunnel/action`; no correlated terminal result | Robot node publishes an `actionId`-correlated `ResultEnvelope` to `robot/tunnel/result` (both the in-process flow and the live Zenoh node). | `sim_arm_01/flow/relay.py` (`RobotNode`), `sim_arm_01/node.py` |
| Success not consumed before settlement; failure/timeout could settle | `settle()` is called **only** inside the result handler and **only** for `status == "success"`. Failure, timeout (no result), 402/400/409 never settle. | `sim_arm_01/flow/relay.py`, `test/test_flow.py` |
| Missing registry profile / pricing / example / validation package | Five discoverable profiles + example envelopes + this report. | `profiles/`, `examples/example-envelopes.jsonc` |
| Missing 402 → verified receipt → simulate → result evidence | Reproducible transcript covering 402 / 400 / 409 / success / failure. | `sim_arm_01/flow/demo.py` |
| Missing Sim-to-Sim validation/recording | MuJoCo vs PyBullet convergence harness + test. | `sim_arm_01/sim_to_sim.py`, `test/test_sim_to_sim.py` |
| `test_unreachable_target_fails` did not assert failure; target was clamped/reachable | Mapper no longer clamps the target; the test uses `[5.0, 5.0]` (outside ±3.14) and asserts `success is False`, large error, and step-budget exhaustion. | `sim_arm_01/mapper.py`, `test/test_simulator.py` |

---

## 1. Full pay-to-actuate flow (accepted → simulate → terminal result → settle)

Command: `python -m sim_arm_01.flow.demo`

```
[ submit ] paid move_to_pose [1.0, -0.5] action=01c9d16b
[  ack   ] {"status": "accepted", "actionId": "01c9d16b-..."}
[ result ] status=success joint_error=0.0038 steps=103
[ settle ] settled=True  (expect True)

[ submit ] unpaid move_to_pose
[  ack   ] {"status": "rejected", "httpStatus": 402, "code": "PAYMENT_REQUIRED", ...}
[ verify ] no result published, robot never actuated  (expect 402)

[ submit ] tampered paramsHash
[  ack   ] {"status": "rejected", "httpStatus": 400, "code": "PARAMS_TAMPERED", ...}

[ submit ] replayed idempotencyKey=dup-key-001
[  ack   ] {"status": "rejected", "httpStatus": 409, "code": "DUPLICATE_REQUEST", ...}

[ submit ] paid move_to_pose [5.0, 5.0] (unreachable)
[  ack   ] {"status": "accepted", "actionId": "a79e042a-..."}
[ result ] status=error code=ACTION_FAILED joint_error=2.6304
[ settle ] settled=False  (expect False)
```

| Case | Ack | Terminal result | Settled |
|---|---|---|---|
| Paid, reachable target | `accepted` | `success` | **yes** |
| Unpaid | `402`, never published | none (robot never actuates) | no |
| Tampered paramsHash | `400`, never published | none | no |
| Replayed idempotencyKey | `409`, never published | none | no |
| Paid, unreachable target | `accepted` | `error` / `ACTION_FAILED` | **no** |

The failure case is a genuine physical failure: `[5.0, 5.0]` is outside the
reachable ±3.14 rad range, the actuator saturates at its limit, and the arm
never converges — reported honestly as `ACTION_FAILED`, not a fabricated code.

## 2. Tests

Command: `pytest test/ -v`

- `test_flow.py` — accepted/pending → simulate → actionId-correlated terminal
  result → success-only settlement, plus 402 / 400 / 409 and the failure/no-settle case.
- `test_simulator.py` — reaches a reachable pose; genuinely fails on an unreachable one.
- `test_sim_to_sim.py` — MuJoCo and PyBullet agree (skipped if pybullet absent).

## 3. Sim-to-Sim validation

Command: `python -m sim_arm_01.sim_to_sim`

```
            target | mujoco err | pybullet err | joint diff | consistent
--------------------------------------------------------------------------
       [1.0, -0.5] |     0.0038 |       0.0005 |     0.0037 | YES
        [0.5, 0.5] |     0.0025 |       0.0005 |     0.0017 | YES
       [-1.2, 0.8] |     0.0039 |       0.0006 |     0.0039 | YES
       [2.0, -1.5] |     0.0043 |       0.0004 |     0.0043 | YES
--------------------------------------------------------------------------
SIM-TO-SIM VALIDATION PASSED
```

The identical `move_to_pose` controller drives two independent engines; the
largest cross-engine joint disagreement is ~0.004 rad.

---

## Scope note on payment (read this)

This is a **simulator-only Tier 1** submission. The payment layer verifies that a
request carries a receipt (`txHash`), that its `paramsHash` matches its params
(tamper protection), and that its `idempotencyKey` is not replayed — and it gates
settlement on a successful terminal result. It does **not** perform live on-chain
x402 settlement; the receipts in the demo are fixtures (`0xVALID`), not Base
Sepolia transactions. What is demonstrated is the **settlement-gating logic and
payment-safety envelope**, end-to-end and reproducibly. Wiring the same
`PaymentGuard.verify_request` / `settle` hooks to a real x402 verifier + on-chain
settlement is the Tier-2/hardware step and requires live wallet credentials.

## Environment

| Component | Version |
|---|---|
| Python | 3.11 |
| mujoco | 3.10.0 |
| pybullet | 3.2.7 |
| numpy | — |
