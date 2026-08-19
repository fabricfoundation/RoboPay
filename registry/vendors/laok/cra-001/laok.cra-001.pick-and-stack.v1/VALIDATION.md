# Validation report — cra-001 (RoboPay Tier 1)

Self-audit against the 13 acceptance criteria and the Tier 1 rubric.
Every row names the file that implements it and the test that proves it.

Reproduce everything:

```bash
cd bridge/cra-001
pip install -r requirements.txt
pytest -q                 # 76 tests
python -m flow.demo --all # the paid flow across all four scenes
```

Result on the reference platform (`ubuntu-22.04`, Python 3.11/3.12):
**76 passed**. On Windows 9 tests skip (no `pybullet` / `zenoh` wheels);
their call paths are still covered by `tests/bullet_stub.py`.

---

## 1. End-to-End Paid Flow ✅

Ten steps, one command (`python -m flow.demo --all`).

| step | where |
|---|---|
| 1 discover skills | `flow/profiles.py::list_skills` ← `profiles/skills.yaml` |
| 2 request action unpaid | `flow/relay.py` → `402` + `accepts` |
| 3 robot untouched | execution counter printed by `flow/demo.py` |
| 4 pay | x402 receipt with `txHash` |
| 5 submit paid action | `flow/envelope.py::TaskEnvelope` (six fields) |
| 6 publish | `robot/tunnel/action` (`flow/zenoh_transport.py`) |
| 7 execute | `simulator.py` (MuJoCo) / `simulator_pybullet.py` |
| 8 result | `robot/tunnel/result`, correlated by `actionId` |
| 9 settle / skip | `flow/payment.py::SettlementLedger` |
| 10 replay rejected | `flow/relay.py` idempotency guard |

**Evidence** — `tests/test_flow.py` (4), `tests/test_transport.py` (7),
`tests/test_simulator.py::TestMuJoCoPick::test_relay_settles_only_on_success`.

## 2. Zenoh Bridge ✅

Official topics, unmodified: `robot/tunnel/action` / `robot/tunnel/result`,
declared in `profiles/robot.profile.yaml` and defined in
`flow/zenoh_transport.py` (`ACTION_TOPIC` / `RESULT_TOPIC`). Endpoint
`tcp/127.0.0.1:17447`, mode `peer`, JSON payloads, correlation by `actionId`.
Zenoh drives the simulator directly — no ROS bridge in between.

`LoopbackTransport` is the platform fallback: identical topics, identical
envelope, identical `RobotHandler`. It is not a payment shortcut — it replaces
the wire only.

**Evidence** — `tests/test_transport.py`,
`tests/test_profiles.py::TestRobotProfileMatchesSpec::test_topics_match_the_transport_module`,
`…::test_endpoint_and_mode_match_the_transport_module`.

## 3. Real Action / 402 + txHash + six fields ✅

* 402 challenge is generated from `profiles/payment-policy.yaml` +
  `profiles/skills.yaml` — price, network, asset, `payTo`, `maxTimeoutSeconds`.
* `X-PAYMENT` receipt must carry a `txHash` (`flow/payment.py::verify_payment`).
* Envelope preserves `actionId, robotId, skillId, idempotencyKey, paramsHash,
  payment`; `paramsHash` is a canonical SHA-256 so params cannot be swapped in
  flight.
* Rejections: no payment → 402; no `txHash` → 402; unknown skill → rejected;
  unknown/invalid params → rejected before dispatch; replayed key → rejected.

**Evidence** — `tests/test_flow.py::TestPaymentFlow` (4),
`tests/test_profiles.py::TestProfilesDriveTheRelay` (5),
`tests/test_transport.py` (six-field preservation),
`tests/test_profiles.py::TestFunctionsManifest::test_envelope_keeps_the_six_required_fields`.

## 4. Skill Registration & Pricing ✅

`pick_and_stack`, 0.10 USDC (`100000` atomic units, 6 decimals) on Base Sepolia,
settlement `on-success-only`. Declared once in `profiles/skills.yaml` and read
at runtime — the price in the 402 response is not hard-coded anywhere.
Discovery (`list_skills`) is free and returns the params schema plus the list of
failure modes.

**Evidence** —
`tests/test_profiles.py::TestSkillsCatalogMatchesCode::test_price_is_declared_once_and_is_coherent`,
`…::TestProfilesDriveTheRelay::test_402_challenge_carries_the_catalogue_price`,
`…::test_discovery_is_free_and_lists_the_price`.

## 5. Success / Failure Semantics ✅

Success requires **all three**: `graspState == attached`,
`contactForce ≥ 0.30 N`, `objectLifted ≥ 0.030 m`.

| scene | reason | measured (MuJoCo) | settles |
|---|---|---|---|
| `cube` | `picked` | lifted 0.1313 m, force 9.81 N, 260 steps | ✅ |
| `unreachable` | `unreachable` | stops short at full stretch, 70 steps | ❌ |
| `collision` | `collision` | obstacle contact at step 24 | ❌ |
| `timeout` | `timeout` | budget 60 exhausted mid-approach | ❌ |
| weak grip | `grasp_failed` | force/lift below threshold | ❌ |

Failures are **physical, not simulated branches**: the scene table in
`arm_spec.py` moves the cube out of the work envelope, puts a pillar on the
path, or clips the step budget. The controller is unchanged in all four cases.

**Evidence** — `tests/test_simulator.py` (5),
`tests/test_profiles.py::TestSkillsCatalogMatchesCode::test_declared_failure_modes_are_the_real_ones`,
`python -m flow.demo --all` summary table.

## 6. Scope Classification ✅

`classification: simulator`, `simulationOnly: true`,
`realWorldActuation: false`, `gpuRequired: false` in
`profiles/robot.profile.yaml`; restated at the top of `README.md`. No hardware
SDK, no motor driver and no teleop channel exist in the tree.

**Evidence** — `tests/test_profiles.py::TestRobotProfileMatchesSpec::test_scope_is_declared_simulation_only`.

## 7. Payment Safety — no settle on failure ✅

Policy switches in `profiles/payment-policy.yaml`, all `false`:
`settleOnFailure`, `settleBeforeExecution`, `captureOnAuthorization`,
`executeWithoutPayment`, `doubleExecutionOnReplay`.

Implementation: `flow/relay.py` calls `ledger.settle()` only when the robot
result is `completed`, otherwise `ledger.skip()`. Nothing is captured at
authorization time, so a failure needs no refund path. The idempotency key is
recorded **after** the execution attempt, so a crashed attempt is never
silently retried and a replay never re-settles.

**Evidence** — the policy file lists five test IDs and
`tests/test_profiles.py::TestPaymentPolicy::test_safety_proof_tests_actually_exist`
asserts each of them exists:

* `tests/test_flow.py::TestPaymentFlow::test_failure_no_settle`
* `tests/test_flow.py::TestPaymentFlow::test_unpaid_rejected`
* `tests/test_simulator.py::TestMuJoCoPick::test_relay_settles_only_on_success`
* `tests/test_sim2sim.py::TestPyBulletBackendContract::test_failure_still_blocks_settlement`
* `tests/test_sim2sim.py::TestSimToSimAgreement::test_failures_never_settle_on_either_engine`

## 8. Robot Identity & Wallet Binding ✅

`robotId: cra-001`, `profileId: laok.cra-001.pick-and-stack.v1`,
identical across all five manifests (asserted). Wallet material is bound by
environment variable name only — `FABRIC_ARM_WALLET_ADDRESS`,
`FABRIC_ARM_PRIVATE_KEY`, `FABRIC_ARM_PAYTO_ADDRESS`, `X402_FACILITATOR_URL`.
The repository contains no key material and no `.env`.

**Evidence** —
`tests/test_profiles.py::TestManifestsExist::test_identity_is_consistent_across_manifests`,
`…::TestRobotProfileMatchesSpec::test_wallet_binding_is_env_only`,
`…::TestPaymentPolicy::test_no_private_key_literal_anywhere_in_the_bridge`
(scans every `.py` / `.yaml` / `.md` for 64-hex-digit literals),
`…::test_payto_address_comes_from_the_environment`.

## 9. Reproducibility ✅

Three commands from a clean checkout (§1). CPU-only wheels, no compilation, no
GPU, no external Zenoh router, no network access during execution. Default
payment mode is `mock`, so the demo is fully offline-reproducible.
CI runs the same commands on `ubuntu-22.04` for Python 3.11 and 3.12.

**Evidence** — `requirements.txt`, `README.md` §1,
`.github/workflows/cra-001-bridge.yml`.

## 10. Demo Evidence ✅

`python -m flow.demo --all` prints, per scene, the payment state, the
settlement decision and the simulator readout (stage, grasp state, lift
distance, contact force, steps used, collision count) — then a summary table
and an explicit `PASS/FAIL` on the settlement policy. `python -m flow.demo
--object <scene>` prints the full 10-step trace including the raw 402 body and
the execution counter proving the robot was not contacted before payment.

## 10b. Payment boundary: x402 verification (D7, PR #70 review response)

`flow/x402.py` replaces the D1 mock with a protocol-level x402 verifier:

* the receipt must match the 402 challenge from `payment-policy.yaml`
  (amount `0.10` USDC, network `base-sepolia`, asset address);
* the `txHash` must be a well-formed `0x` + 64-hex chain hash;
* a `(payer, txHash)` pair can never be reused (replay protection lives in
  the relay's verifier instance — one per relay, spanning the relay lifetime);
* every failure raises `X402Error` (subclass of `PaymentError`) and the relay
  answers **402**, so the robot is never contacted with an unverified payment.

Verification runs **protocol-level by default** (deterministic, offline,
CI-safe). A live call to the official facilitator (`https://x402.org/facilitator`)
can be enabled per-verifier (`online=True`) and its evidence is tagged
`verification: facilitator` when reachable, otherwise honestly tagged
`verification: protocol` with `reachable: false` — the demo never claims an
on-chain verification it did not perform.

**Evidence** — `flow/x402.py`, `flow/payment.py::verify_payment`,
`tests/test_x402.py` (17 tests: challenge shape, bad amount/network/asset,
malformed txHash, replay, relay-never-touches-robot for unverified payments),
`python -m flow.demo --payment-mode x402 --all`.

## 11. Code Quality ✅

Layered and swappable: payment / transport / execution never learn about each
other. `flow/executor.py::make_simulator` is the single robot-adapter seam —
adding a real robot is one branch. `arm_spec.py` is the single source of truth
for both engines. All tuneables (topics, endpoint, price, thresholds, scene
table, step budgets) live in `arm_spec.py` or the YAML manifests; none are
hard-coded at call sites. 76 tests, no secrets, no dead imports in the shipped
path. Superseded prototypes are quarantined in `experiments/` with a note on
why they were replaced.

## 12. Rubric self-score

| Category | Pts | Claim |
|---|---:|---|
| Full Fabric → Zenoh → robot flow | 25 | 25 — 10 steps, one command, real topics |
| Real action executed & visually proven | 20 | 18 — physics-measured lift + force on two engines; screen recording pending |
| Correct success/failure handling | 15 | 15 — 1 success + 4 distinct physical failures |
| Payment safety / no-settle-on-failure | 15 | 15 — policy + code + five named tests |
| Reproducible README & setup | 10 | 10 — 3 commands, CPU-only, CI green |
| Native robot-stack integration | 10 | 10 — Zenoh drives MuJoCo directly, no ROS |
| Code quality & tests | 5 | 5 — 76 tests, manifests validated against code |
| **Total** | **100** | **98 self-assessed** (pass = 75) |

## 13. Non-acceptable behaviours — explicitly avoided

| forbidden | status |
|---|---|
| mock-only execution | ❌ avoided — MuJoCo rigid-body dynamics, contact forces read from the solver |
| object teleported / animated | ❌ avoided — cube is a free body; contact-gated grasp; `replayedAnimation: false` asserted |
| no failure case | ❌ avoided — four distinct physical failure scenes |
| settle on failure | ❌ avoided — policy switch + five tests |
| double execution on replay | ❌ avoided — idempotency guard + execution counter assertions |
| secrets in repo | ❌ avoided — env-only, repo-wide scan test |
| GPU / ROS / hardware dependency | ❌ avoided — CPU-only requirements, scope declared `simulator` |

---

## Open items

* **Live x402 facilitator settlement** — verification now runs through
  `flow/x402.py` (protocol-level by default, facilitator-call opt-in via
  `online=True`). On-chain settlement of USDC on `eip155:84532` remains a
  swap point (`SettlementLedger`); it requires a funded wallet and is not
  part of the offline-reproducible demo.
* **Screen recording** — the demo already emits the full trace to stdout; a
  capture will be attached to the PR description.

## R7. Controller is policy / state-machine driven (not fixed-joint replay)

Requirement R7: the skill is driven by a **closed-loop IK + keyframe state
machine**, not a fixed joint playback:

- `simulator.py::_ik_dls(q0, target, iters=120, lam=0.08)` runs a **Damped
  Least-Squares IK** on every call, re-solving the 6-DoF end-effector error
  against the *current* joint state — a feedback loop, not a looked-up
  trajectory.
- `KEYFRAMES` + `STAGE_STEPS` form the state machine; `_apply(q, grip)` writes
  the solved joints to the engine each step.
- Because IK re-solves from the live state, the same controller produces a
  successful pick on a centred cube and a physical failure when the target is
  out of reach or blocked. There is no separate "fail branch" — the physics
  gates it. `replayedAnimation` is asserted `false`.

### Evidence (motion is physics-gated, not a clip)
- `tests/test_simulator.py` asserts success/failure come from measured physics
  (contact force, lift, collision count), not from a fixed branch.
- `python -m flow.demo --all` prints the per-stage readout (stage / grasp /
  lift / force for arms; phase / foot-target / torque for G1), proving the
  controller runs live every step.
- `docs/evidence/robopay_evidence.gif` shows the same run with the
  `402 → paid → action_id → physics → settle` sequence in one frame.

