# Validation report

## Provenance and task

- MuJoCo source: `unitreerobotics/unitree_mujoco` at
  `ae6a8403e272733e9996ef59990880330496177f`.
- Webots source: `unitreerobotics/unitree_ros` at
  `daadf41ee9afce8f90fdc09a98506012691fa122`.
- Model: official G1 29-DoF MJCF and URDF, both BSD-3-Clause.
- Task: measured-state inspection of left, center, and right targets using the
  official waist and arm joints.

The task is not prerecorded. Both engines execute
`unitree-g1-29dof-active-inspection-v1-shared`, observe joint positions, and
advance only after the active goal remains within 0.075 rad for 0.55 seconds.
The pelvis fixture and feet-on-floor scope are disclosed in both engines; no
walking or autonomous-balance claim is made.

## Mandatory acceptance gates

The `unitree-g1-tier1.yml` workflow requires:

1. Real Go Tunnel build/tests and registry drift validation.
2. Official-model MuJoCo execution confirming all three targets.
3. Webots R2025a execution and Sim-to-Sim score 1.0 with the shared policy.
4. Paid-shaped `isValid: false` and missing-verdict responses returning 402
   before `PostAction`, with zero ActionEvents, state changes, and settlements.
5. Correlated success/failure, failure/timeout non-settlement, paid stop,
   durable idempotency, and payment-fingerprint replay defense.
6. Continuation-frame WebSocket assembly and first paid action after readiness,
   without any warm-up action.
7. Live Base Sepolia settlement only on trusted push/workflow-dispatch runs.

## Current validation

- [x] Exact official source revisions and canonical hash verification.
- [x] MuJoCo 3.3.0: 3/3 targets; confirmations at 1.192, 2.418, and
  3.638 simulated seconds.
- [x] Webots R2025a: 3/3 targets; confirmations at 1.104, 2.056, and
  3.008 simulated seconds.
- [x] Sim-to-Sim score 1.0 with exact shared-policy match.
- [x] Full Webots viewer uses official meshes; CI derivatives preserve all 29
  actuated joints while bounding visual complexity only.
- [x] Actual-model safe stop and minimum-speed execution are covered.
- [x] Policy, bridge, actual-model, and Webots-model suite: 13 tests passed.
- [x] Real Tunnel Go suite passed, including verification-before-publication,
  execution-gated settlement, timeout, and durable replay tests.
- [x] Paid-shaped `isValid: false` and missing-verdict probes returned HTTP
  402 with zero ActionEvents, simulator state changes, and settlement calls.
- [x] Failure, timeout, idempotency replay, and payment replay produced zero
  settlement calls through the real Tunnel and Zenoh transport.
- [x] Fabric Gateway dry run: subscriber readiness, no warm-up action, robot
  and skill discovery, unpaid HTTP 402, and 300-second authorization window;
  no payment was signed or submitted.
- [ ] Trusted Base Sepolia current-head receipt captured.
- [ ] Paid-action simulator recording paired continuously with terminal and
  matching BaseScan evidence.

Machine-readable outputs are written under
`bridge/unitree/g1_inspection_bridge/artifacts/`. Generated models, PROTOs,
unreviewed runtime receipts, logs, and secrets remain ignored. Only reviewed
evidence should be copied into `docs/evidence/` and bound in the manifest.

## Safety and limitations

- Simulator-only; no physical Unitree G1 has been validated.
- The fixture constrains the pelvis; the 29 articulated joints retain official
  limits and are read from simulator state.
- `stop` commands the neutral pose and zeros MuJoCo articulated velocity.
- Unknown actions and out-of-contract parameters fail before simulator entry.
- Settlement occurs only after an exactly correlated successful result.
