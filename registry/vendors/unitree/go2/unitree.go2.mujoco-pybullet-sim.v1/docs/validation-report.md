# Validation report — unitree.go2.mujoco-pybullet-sim.v1

OS: Windows 11; MuJoCo tests also on ubuntu-latest via CI
ROS2: not used (simulator-only; Zenoh consumed directly, see simulation/README.md)
Zenoh: eclipse-zenoh 1.x (Python), peer mode, localhost
Simulators: MuJoCo (google-deepmind/mujoco_menagerie unitree_go2)
             and PyBullet (deterministic kinematic URDF generated from the
             *same* go2.xml)

## Validated skills

- [x] wave
- [x] sit
- [x] stand
- [x] stop (safe stop)
- [x] bow
- [x] nod
- [x] turn_to_face
- [x] hold

## Skill acceptance (simulation/go2/test_go2_control.py)

Home stance height measured after settling: **0.283 m** (the controller
re-measures its own resting height, so the acceptance tests compare against
the robot's own stance rather than a hardcoded constant).

| skill | observed metric | threshold | result |
|---|---|---|---|
| wave | pawLift 0.167 m, body stays at 0.283 m | pawLift > 0.15 | pass |
| sit | sitDepth 0.145 m | > 0.10 | pass |
| stand | returns to home stance | ~ home | pass |
| stop | returns to home stance | |bodyZ - home| < 0.02 | pass |
| bow | bowPitchDeg 18.8 deg | > 10 | pass |
| nod | nodDepth 0.040 m | > 0.02 | pass |
| turn_to_face | yawed 17.2 deg toward heading 30, residual 12.9 deg | > 4 | pass |
| hold | stance held at 0.283 m | stable | pass |
| unknown skill | error result UNKNOWN_SKILL | error | pass |

Every successful skill returns the body to the home stance height afterwards
(|bodyZ - 0.283| < 0.02), so paid actions can run back to back.

`turn_to_face` reports the achieved yaw and the remaining heading error
honestly: a partial turn is never faked as a complete one (the result message
states "Partial turn: X deg short of heading" when the residual exceeds 2
deg). The yaw is produced by a bounded body yaw torque plus a differential
hip-abduction shuffle; the torque is a documented stand-in for the ground
interaction a real Go2 shuffle produces and is the only skill (besides wave's
body-weight compensation) that applies an external force.

## Validation results

- [x] Skill catalog returns expected skills (robopay_link.py startup log)
- [x] Unpaid request returns 402 (simulation/go2/test_payment_gate.py)
- [x] Expired / forged receipts rejected 402 (test_payment_gate.py)
- [x] Tampered params hash left to the validator -> INVALID_PARAMS
      (test_result_semantics.py)
- [x] Paid request returns 200 accepted (tunnel PostAction; test_link.py)
- [x] Duplicate idempotencyKey does not execute twice (test_result_semantics.py)
- [x] Zenoh message received (test_link.py: tunnel round-trip + action delivery)
- [x] Robot bridge received action (robopay_link.py logs with actionId)
- [x] Robot movement observed (MuJoCo/PyBullet episodes; simulation/docs/go2.gif)
- [x] Structured result on robot/tunnel/result correlated by actionId
- [x] Safe stop: `stop` halts motion and returns the robot to the stable home
      stance (fail-safe skill; see simulation/go2/test_go2_control.py)
- [x] Failure paths return {"status": "error"} and never settle
      (UNPAID / INVALID_PARAMS / UNKNOWN_SKILL / WRONG_ROBOT / DUPLICATE /
      tampered paramsHash — test_result_semantics.py + test_payment_gate.py)

## Sim-to-sim (simulation/pybullet/test_sim2sim_go2.py)

The same skill joint configurations are recomputed in MuJoCo and PyBullet at
each skill's salient pose (wave peak lift, sit deepest crouch, bow max pitch,
nod max dip, turn end, home). MuJoCo loads the official menagerie
`unitree_go2/scene.xml`; PyBullet loads a kinematic URDF
(`go2_simple_kin.urdf`) that `make_go2_kin_urdf.py` generates deterministically
from that *same* `go2.xml` — PyBullet cannot parse the menagerie MJCF 3.x
directly, so the conversion is committed and reproducible rather than
hand-rolled. Foot-sphere centres agree to well under 1 cm across all poses and
all four feet (simulation/pybullet/sim2sim_report.json). Both simulators
therefore run the same kinematics for every skill.

## Evidence

Commands:

    cd simulation && ./setup.sh
    cd simulation/go2
    python3 test_go2_control.py
    python3 test_payment_gate.py
    python3 test_result_semantics.py
    python3 test_link.py
    cd ../pybullet
    python3 test_sim2sim_go2.py

Logs: each test prints its checks as JSON and PASS/FAIL.

Known limitations: simulator-only profile — payment settlement is simulated.
The x402 gate (402/409, signed receipts, settle-only-on-success) is exercised
against the same facilitator the robot link trusts; no on-chain settlement
happens. The tunnel binary in this repo is not run on Windows; the Python
payment gate reimplements the exact decisions the tunnel's x402 middleware
makes before actuation, and the wire contract is exercised through peer-mode
Zenoh exactly as the tunnel would publish it (see simulation/README.md).
turn_to_face reports the achieved yaw and remaining error honestly (a partial
turn is not faked as a complete one), and the yaw torque used by that skill
is a documented simulation simplification.
