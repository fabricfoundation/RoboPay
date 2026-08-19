# k1-001 bridge -- MuJoCo simulated active inspection (Tier 1)
#
# Booster K1 is a 22-DoF fixed-base inspection robot with a wrist-mounted camera.
# This bridge simulates the active inspection task: moving the camera to inspect
# three targets (left, center, right) in sequence and confirming each is within
# the camera's field of view.
#
# The simulation uses a simplified 6-DOF serial arm model that captures the
# essential kinematics for the inspection trajectory.

## Validation

- MuJoCo: 3/3 targets confirmed in inspection scenario
- Sim-to-Sim: MuJoCo vs PyBullet consistency verified
- local Python suite: tests pass
- registry profile contract: passed
- dependency check and secret scan: passed

## Scope and evidence

This is simulator-only. The official K1 is a fixed-base inspection robot with
22 degrees of freedom. This profile does not claim autonomous movement or
physical-robot validation. Trusted Base Sepolia settlement evidence and the
paired paid-action recording are produced only by the configured live workflow.

## Key features

- **Real physics**: Uses MuJoCo physics engine with rigid body dynamics,
  collision detection, and contact forces.
- **Closed-form IK**: Joint targets are computed analytically at import time,
  not via runtime IK loops.
- **Deterministic**: No stochastic elements; same input always produces same output.
- **Sim-to-sim verification**: Both MuJoCo and PyBullet backends produce
  consistent results for the same inputs.
- **Fail-closed payment gate**: Only paid actions are executed; unpaid requests
  receive HTTP 402.
- **No settlement on failure**: Failed or timed-out executions never settle payment.
- **Idempotency protection**: Replay of the same action is rejected.
