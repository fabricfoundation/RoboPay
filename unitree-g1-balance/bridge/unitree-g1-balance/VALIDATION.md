# Validation report — unitree-g1-arm-001 (RoboPay Tier 1)

Self-audit against the Tier 1 rubric, focused on requirement **R7** (controller
is policy / state-machine driven, not a fixed-joint replay) plus the end-to-end
paid flow that exercises it.

Reproduce:

```bash
cd bridge/unitree-g1-arm-001
pip install -r requirements.txt
pytest -q
python -m flow.demo --all
```

## 1. End-to-End Paid Flow (summary)

`python -m flow.demo --all` runs the ten steps: discover → 402 (no payment) →
robot untouched → pay (x402 `txHash`) → submit paid action (six-field envelope,
correlated by `actionId`) → publish on `robot/tunnel/action` (Zenoh) → execute
in MuJoCo → result on `robot/tunnel/result` → settle on success only → replay
rejected. The skill executed is **`balance_recover`** (real MuJoCo rigid-body dynamics,
contact forces read from the solver).

## R7. Controller is policy / state-machine driven (not fixed-joint replay)

Requirement R7: the skill is driven by a **phase / foot-target state machine
with a PD feedback controller**, not by replaying joint angles:

- `simulator.py::_foot_targets(step, obstacles, advancing)` computes the swing /
  stance foot targets **each simulation step** from the step counter, the live
  obstacle list and the `advancing` flag — the policy, not a recording.
- `MuJoCoSimulator._apply_control(targets)` runs a **PD controller** (position
  error → torque) every step; joint torques are bounded (torque-limited), so the
  robot can actually fall when pushed hard — a real physical failure, not a
  scripted stop.
- `balance_recover` / `move_forward` / `pick_and_carry` select the target phase
  set from skill parameters + sensed state; the same engine yields a recovered
  stance or a saturated fall depending on the perturbation magnitude. No joint
  clip is replayed; `replayedAnimation` is asserted `false`.

### Evidence (motion is physics-gated, not a clip)
- `tests/test_simulator.py` asserts success/failure come from measured physics
  (contact force, lift, collision count), not from a fixed branch.
- `python -m flow.demo --all` prints the per-stage readout (stage / grasp /
  lift / force for arms; phase / foot-target / torque for G1), proving the
  controller runs live every step.
- `docs/evidence/robopay_evidence.gif` shows the same run with the
  `402 → paid → action_id → physics → settle` sequence in one frame.


## 2. Payment safety — no settle on failure

`profiles/payment-policy.yaml` keeps `settleOnFailure` / `settleBeforeExecution`
/ `executeWithoutPayment` / `doubleExecutionOnReplay` all `false`.
`flow/relay.py` calls `ledger.settle()` only when the robot result is
`completed`; otherwise `ledger.skip()`. Idempotency key is recorded after the
execution attempt, so a crash is never silently retried and a replay never
re-settles.

## 3. Scope

`classification: simulator`, `simulationOnly: true`, `realWorldActuation:
false` in `profiles/robot.profile.yaml`. No hardware SDK, no motor driver, no
teleop channel in the tree. Wallet material is env-only; the repo contains no
key material.
