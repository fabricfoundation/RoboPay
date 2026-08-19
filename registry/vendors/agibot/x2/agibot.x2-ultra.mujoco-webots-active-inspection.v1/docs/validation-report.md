# Validation report

## Provenance and task

- Official source: `AgibotTech/agibot_x2_urdf`
- Pinned commit: `77f43eb0904dae4c48ccd9154fee824f8ffd4d38`
- Model: `X2_URDF-v1.4.0/X2-Ultra.xml`, `X2-Ultra.urdf`, and
  `X2-Ultra_simple_collision.urdf`
- Exact hashes: MJCF `bfccc266...a340f0`, URDF `728952c1...34328`,
  Webots URDF `e6ce8a43...d841ac`
- License: MulanPSL-2.0, copied with each downloaded model checkout
- Task: closed-loop inspection of left, center, and right targets using the
  official X2 waist, head, and arm joints

The task is not a prerecorded animation. Both engines run
`agibot-x2-active-inspection-v1-shared`, read measured joint positions, and
advance only after the requested pose remains within 0.075 rad for 0.55 s.
MuJoCo applies torque PD plus model-derived gravity/Coriolis compensation;
Webots applies the same goals through position motors.

The pelvis safety fixture is explicit in both engines and both feet remain at
the floor. This Tier 1 profile makes no walking or autonomous-balance claim.

## Mandatory acceptance gates

The workflow requires:

1. Real Go Tunnel build and tests plus registry/schema validation.
2. Official-model MuJoCo execution confirming all three targets.
3. Webots execution and Sim-to-Sim score 1.0 with an identical policy contract.
4. Paid-shaped `isValid: false` and missing-verdict requests returning HTTP 402
   before `PostAction`, with zero ActionEvents, simulator commands, and
   settlement calls.
5. Correlated success/failure, execution-gated settlement, paid stop, failure
   and timeout non-settlement, durable idempotency, and payment replay defense.
6. WebSocket continuation-frame assembly and the first paid action after a
   readiness handshake, without a warm-up action.
7. Base Sepolia settlement evidence only on trusted push/workflow-dispatch runs.

## Current local validation

- [x] Pinned official model checkout and exact hash verification.
- [x] MuJoCo 3.3.0: 3/3 targets, success in 5.750 simulated seconds; target
  confirmations at 2.488, 4.366, and 5.748 s.
- [x] Webots R2025a: 3/3 targets, success in 2.768 simulated seconds using the
  same policy ID and parameters.
- [x] Sim-to-Sim: score 1.0 and exact shared-policy match across MuJoCo and
  Webots.
- [x] Webots provenance split: the operator viewer resolves every original
  upstream STL vertex; the headless CI PROTO uses visual-only 6,000-face/link
  derivatives while preserving all 31 actuated joints, kinematics, inertias,
  limits, controller inputs, and policy state.
- [x] MuJoCo policy and bridge contract suite: 11 tests passed, including
  correlation, invalid contracts, foreign-robot isolation, paid stop, and
  actual-model safe stop.
- [x] Linux container: real Tunnel build and complete Go suite passed with
  Zenoh C 1.9.0, including durable replay and execution-gated settlement.
- [x] Linux container: paid-shaped `isValid: false` and missing verdict both
  returned HTTP 402 with zero ActionEvents, simulator commands, and settlement
  calls; failure, timeout, idempotency replay, and payment replay also made zero
  settlement calls.
- [x] Real Fabric Gateway dry-run from Windows through the WSL-hosted Tunnel:
  subscriber readiness, no warm-up action, correct skill discovery, and unpaid
  HTTP 402; no payment was signed or submitted.
- [x] Trusted Base Sepolia action and settlement: source commit `85fc510`,
  unpaid HTTP 402, first paid HTTP 202, correlated action
  `x2-active-inspection-1787121070`, all three targets confirmed, and settlement
  transaction `0x03032a66...d8d8e5` on Base Sepolia.
- [x] Continuous source-bound visual recording: 43.966667 s at 1280x720 with
  the readable terminal and complete native MuJoCo viewer shown together,
  including the matching BaseScan success page. Recording SHA-256:
  `66205eb1596fabec391baa84c49bf7734fb1cf3587a23a73202d54c8c4cfcaf6`.

Machine-readable local outputs are written to
`bridge/agibot/x2_inspection_bridge/artifacts/`. The reviewed live receipt and
recording are intentionally copied into `docs/evidence/` and bound by the
manifest; generated models, PROTOs, unreviewed runtime receipts, and secrets
remain ignored.

## Safety and limitations

- Simulator-only; no physical X2 hardware has been validated.
- The fixture constrains only the pelvis. All 31 articulated joints retain the
  official model limits and are observed from simulator state.
- `stop` commands the neutral articulated pose and zeros MuJoCo joint velocity.
- Unknown actions and out-of-contract parameters fail before simulator entry.
- Settlement occurs only after an exactly correlated successful result.
