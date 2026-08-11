# Validation report — boston_dynamics.spot.mujoco-pybullet-sim.v1

OS: Windows 11; MuJoCo tests also on ubuntu-latest via CI
ROS2: not used (simulator-only; Zenoh consumed directly, see simulation/README.md)
Zenoh: eclipse-zenoh 1.x (Python), peer mode, localhost
Simulators: MuJoCo (google-deepmind/mujoco_menagerie boston_dynamics_spot)
             and PyBullet (spot_description `spot_simple_kin.urdf`)

## Validated skills

- [x] wave
- [x] sit
- [x] stand
- [x] stop (safe stop)
- [x] bow
- [x] nod
- [x] turn_to_face
- [x] hold

## Skill acceptance (simulation/spot/test_spot_control.py)

| skill | observed metric | threshold | result |
|---|---|---|---|
| wave | pawLift 0.212 m, body stays at 0.432 m | pawLift > 0.15 | pass |
| sit | sitDepth 0.133 m | > 0.10 | pass |
| stand | standHeight 0.435 m | ~ HOME (0.434) | pass |
| stop | returns to HOME stance, halted at 0.434 m | |bodyZ - 0.434| < 0.02 | pass |
| bow | bowPitchDeg 16.9 deg | > 10 | pass |
| nod | nodDepth 0.055 m | > 0.02 | pass |
| turn_to_face | achievedYawDeg 10.7 deg toward heading 30 | > 4 | pass |
| hold | stance held at 0.434 m | stable | pass |
| unknown skill | error result UNKNOWN_SKILL | error | pass |

Every successful skill returns the body to the home stance height afterwards
(|bodyZ - 0.434| < 0.02), so paid actions can run back to back.

## Validation results

- [x] Skill catalog returns expected skills (robopay_link.py startup log)
- [x] Unpaid request returns 402 (simulation/spot/test_payment_gate.py)
- [x] Expired / forged receipts rejected 402 (test_payment_gate.py)
- [x] Tampered params hash left to the validator -> INVALID_PARAMS
      (test_result_semantics.py)
- [x] Paid request returns 200 accepted (tunnel PostAction; test_link.py)
- [x] Duplicate idempotencyKey does not execute twice (test_result_semantics.py)
- [x] Zenoh message received (test_link.py: tunnel round-trip + action delivery)
- [x] Robot bridge received action (robopay_link.py logs with actionId)
- [x] Robot movement observed (MuJoCo/PyBullet episodes; simulation/docs/spot.gif)
- [x] Structured result on robot/tunnel/result correlated by actionId
- [x] Safe stop: `stop` halts motion and returns the robot to the stable home
      stance (fail-safe skill; see simulation/spot/test_spot_control.py)
- [x] Failure paths return {"status": "error"} and never settle
      (UNPAID / INVALID_PARAMS / UNKNOWN_SKILL / WRONG_ROBOT / DUPLICATE /
      tampered paramsHash — test_result_semantics.py + test_payment_gate.py)

## Sim-to-sim (simulation/pybullet/test_sim2sim.py)

The same skill joint configurations are recomputed in MuJoCo and PyBullet at
each skill's salient pose (wave peak lift, sit deepest crouch, bow max pitch,
nod max dip, turn end, home). Foot-tip positions agree to 0.06 cm maximum
across all six poses and all four feet (simulation/pybullet/sim2sim_report.json).
Both simulators therefore run the same kinematics for every skill.

## Evidence

Commands:

    cd simulation && ./setup.sh
    cd simulation/spot
    python3 test_spot_control.py
    python3 test_payment_gate.py
    python3 test_result_semantics.py
    python3 test_link.py
    cd ../pybullet
    python3 test_sim2sim.py

Logs: each test prints its checks as JSON and PASS/FAIL.

Known limitations: simulator-only profile — payment settlement is simulated.
The x402 gate (402/409, signed receipts, settle-only-on-success) is exercised
against the same facilitator the robot link trusts; no on-chain settlement
happens. The tunnel binary in this repo is not run on Windows; the Python
payment gate reimplements the exact decisions the tunnel's x402 middleware
makes before actuation, and the wire contract is exercised through peer-mode
Zenoh exactly as the tunnel would publish it (see simulation/README.md).
turn_to_face rotates toward the heading with a static-stability shuffle and
reports the achieved yaw and remaining error honestly (a partial turn is not
faked as a complete one).
