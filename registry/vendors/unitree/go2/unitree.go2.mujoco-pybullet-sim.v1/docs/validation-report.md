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
- [x] navigate_obstacle

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

### Obstacle navigation (simulation/go2/test_obstacle_nav.py)

Locomotion is a slow diagonal trot steered by a **shared calf gain** (``kc``,
scaling ``calf = -1.8 + kc*off``) chosen from a measured calibration table
(``STEER_TABLE`` in go2_control.py): the net travel direction is monotone and
reproducible over -21.7 deg (kc=0.88) .. ~0 deg (kc=1.00), so a descending
course is followed as a sequence of straight, low-drift segments. A
potential-field local planner adds repulsion from each obstacle (each cylinder
sits just inside its nominal waypoint segment, so the field must actively
steer around it) and a look-ahead point on the segment keeps the requested
bearing inside the calibrated range near the waypoint. **Success is decided
from the physics state**: obstacle contact is detected from MuJoCo contact
pairs, not a distance estimate. Success requires the goal to be reached with
zero contacts; a timeout returns `TIMEOUT`, a contact returns `COLLISION`.

Measured on the committed course (descending slalom, 3 obstacles, 20 cm
tolerances):

| metric | result |
|---|---|
| waypoints reached | 3/3 |
| final goal distance | 0.099 m (≤ 0.20) |
| obstacle contacts | 0 |
| min obstacle clearance | 0.047 m (> 0) |
| status on success | success |
| status on timeout | error / TIMEOUT |
| status on collision | error / COLLISION |

The course and the measured trajectory are drawn from the real physics run in
`simulation/docs/obstacle_course_map.svg`; the raw numbers land in
`simulation/docs/obstacle_nav_report.json`.

Failure semantics are exercised adversarially
(`simulation/go2/test_adversarial_nav.py`, report in
`simulation/docs/obstacle_adversarial_report.json`): an unreachable goal
returns `error` / `TIMEOUT` and a blocking obstacle returns `error` /
`COLLISION` from real MuJoCo contact pairs (8 simultaneous contact pairs
measured) — the skill never fakes a partial success as a win.

Every successful skill returns the body to the home stance height afterwards
(|bodyZ - 0.283| < 0.02), so paid actions can run back to back.

`turn_to_face` reports the achieved yaw and the remaining heading error
honestly: a partial turn is never faked as a complete one (the result message
states "Partial turn: X deg short of heading" when the residual exceeds 2
deg). The yaw is produced by a differential hip-abduction shuffle (front pair
vs hind pair) driven by a proportional servo; the pose stays inside the
static-stability polygon, so the body stays level and no external torque is
applied to the torso.

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
- [x] Obstacle navigation reaches the goal with zero physics contacts and a
      correct success/error decision (TIMEOUT / COLLISION) — test_obstacle_nav.py
- [x] Durable replay: idempotencyKey / txHash rejected after the store is
      reloaded from disk (tunnel-restart semantics) — test_durable_replay.py
- [x] Optional Base Sepolia settlement module: no-settle-on-failure contract
      and configuration guard — test_settlement.py

## Sim-to-sim (simulation/pybullet/test_sim2sim_go2.py)

The same skill joint configurations are recomputed in MuJoCo and PyBullet at
each skill's salient pose (wave peak lift, sit deepest crouch, bow max pitch,
nod max dip, turn end, home). MuJoCo loads the official menagerie
`unitree_go2/scene.xml`; PyBullet loads a kinematic URDF
(`go2_simple_kin.urdf`) that `make_go2_kin_urdf.py` generates deterministically
from that *same* `go2.xml` — PyBullet cannot parse the menagerie MJCF 3.x
directly, so the conversion is committed and reproducible rather than
hand-rolled. Foot-sphere centres agree to **≤ 0.01 m (1 cm) tolerance, with the
observed worst-case error 0.0002 m = 0.02 cm** across all poses and all four
feet (simulation/pybullet/go2_sim2sim_report.json). Both simulators therefore
run the same kinematics for every skill.

## Sim-to-sim (simulation/webots/test_sim2sim_go2_webots.py)

A genuine Webots supervisor controller is committed in `simulation/webots/`
that re-runs the same skill policies and compares the foot-tip positions
reported by the Webots physics engine against the MuJoCo baseline, writing a
real `go2_webots_sim2sim_report.json` with measured errors. **This harness is
ready but requires the Webots R2025a runtime, which is not bundled in this
repository or in the current CI environment; it is therefore NOT claimed as a
measured result.** Running it under Webots (or in a container that installs
Webots R2025a + the unitree_ros URDF assets) produces the measured report.
Claims in this repository never describe the Webots run as validated until
that report exists with a `pass` verdict.

## Evidence

Commands:

    cd simulation && ./setup.sh
    cd simulation/go2
    python3 test_go2_control.py
    python3 test_payment_gate.py
    python3 test_result_semantics.py
    python3 test_link.py
    python3 test_obstacle_nav.py
    python3 test_adversarial_nav.py
    cd ../pybullet
    python3 test_sim2sim_go2.py

Everything above (plus durable replay and settlement guards) also runs in one
command: `bash simulation/verify_go2_tier1.sh`.

Logs: each test prints its checks as JSON and PASS/FAIL.

## Live on-chain settlement (Base Sepolia, EIP-3009)

The optional settlement module was exercised against the real Base Sepolia
chain (chainId **84532**) using the official Circle **USDC** contract
`0x036CbD53842c5426634e7929541eC2318f3dCF7e` (name `USDC`, EIP-3009 version
`2`, decimals 6 — the version/domain match the module's EIP-712 domain).
Three independent, verifiable settlement transactions settled **1.0 USDC
each** from the payer to the payee using `transferWithAuthorization`:

| # | txHash | block | gasUsed |
|---|---|---|---|
| 1 | `0x64bf269dbc11ca8c24f2b09d038306607035d06669891c84bb3cde029027b6d8` | 45416876 | 100380 |
| 2 | `0x3dfc298391f1a66e1ecbc34ce942b090c00346b98879dae80b8e5d15a7d2d897` | 45416922 | 83288 |
| 3 | `0x6bb1c8edc789068cdba95f556a21720f9d55b564824be07eb758b36815fbb504` | 45416937 | 83256 |

For each successful settlement the receipt logs contain the EIP-3009
`AuthorizationUsed(authorizer, nonce)` event (topic
`0x98de5035...` = `keccak256("AuthorizationUsed(address,bytes32)")`) and the
ERC-20 `Transfer` event of exactly 1.0 USDC from payer to payee. On-chain
post-checks confirmed the payee's balance increased by exactly 1.0 USDC per
transaction and `authorizationState(authorizer, nonce)` returns `true`
(consumed) for every nonce used. All of this was funded **entirely from free
faucets** (Circle USDC faucet + Coinbase CDP Portal Base Sepolia ETH faucet;
total gas spent across all three settlements was under 0.000002 ETH) — no
deposited capital was required to prove settlement.

The **no-settle-on-failure contract** was also proven live: with the relay in
a `timeout` result state, `settle_if_success` short-circuits before any
transaction is built or broadcast, and the relay's on-chain nonce is provably
unchanged before vs. after (relayNonceBefore == relayNonceAfter ==
relayNonceUnchanged == true).

Machine-readable evidence:
`simulation/docs/settlement-proof.json` (success) and
`simulation/docs/settlement-proof-failure.json` (failure).
Reproduce with `simulation/go2/prove_live_settlement.py` (requires
`PRIVATE_KEY`, `PAYEE_ADDRESS`, `BASE_SEPOLIA_RPC_URL`; **never commit keys**).

Known limitations: simulator-only profile. The x402 gate (402/409, signed
receipts, settle-only-on-success) is exercised against the same local
facilitator the robot link trusts — a **simulator gate that mirrors the
tunnel's x402 middleware decision semantics**, not the compiled Go tunnel
binary. Optional on-chain settlement (Base Sepolia, EIP-3009
TransferWithAuthorization) is available via `simulation/go2/
settlement_base_sepolia.py` and has been **verified live on-chain** (see the
"Live on-chain settlement" section above); by default settlement stays on the
local facilitator and no on-chain transaction happens. Replay protection is
file-backed
(`simulation/go2/test_durable_replay.py` proves idempotencyKeys survive a
store restart). The wire contract is exercised through peer-mode Zenoh
exactly as the tunnel would publish it (see simulation/README.md).
turn_to_face reports the achieved yaw and remaining error honestly (a partial
turn is not faked as a complete one); the heading is reached via a
static-stability hip-abduction shuffle, so no external force is applied to
the torso. navigate_obstacle uses static obstacles; dynamic obstacles are not
yet supported.
