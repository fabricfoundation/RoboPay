# Boston Dynamics Spot Tier 1 — paid actions drive the Spot in simulation

**Scope: simulator-only submission.** No physical robot is involved; the
x402 payment gate and the wire contract are exercised end to end in peer-mode
Zenoh, and the on-chain settlement step is simulated.

A paid RoboPay action arriving on the tunnel's Zenoh topic starts a Spot
skill episode on the official MuJoCo model (mujoco_menagerie). Eight skills
are available — wave, sit, stand, stop, bow, nod, turn_to_face, hold — each
driven by a joint-space trajectory controller, not by any recorded motion.
The same joint configurations are recomputed in PyBullet (from the
rai-opensource `spot_simple` URDF) with a measured agreement between the two
engines.

The chain, top to bottom:

    paid action (x402 / AIP) -> tunnel -> Zenoh "robot/tunnel/action"
    -> subscriber -> validate envelope + x402 payment gate -> Spot skill
    -> joint PD on the mujoco_menagerie model -> metrics
    -> result on "robot/tunnel/result" (correlated by actionId)

## Skills

| skill | what happens | measured |
|---|---|---|
| `wave` | front-right paw lifts in an arc and lowers back (body-weight compensation while airborne) | pawLift 0.212 m, body stays at 0.432 m |
| `sit` | body crouches into a sit posture, then returns | sitDepth 0.133 m |
| `stand` | returns to the home standing stance | standHeight 0.435 m |
| `stop` | safe stop: halts all motion and returns to the stable home stance | halted at 0.434 m |
| `bow` | front dips into a play bow | bowPitchDeg 16.9 deg |
| `nod` | full-body greeting bob | nodDepth 0.055 m |
| `turn_to_face` | yaws toward `headingDeg` (static-stability shuffle), reports achieved yaw and remaining error honestly | 10.7 deg toward heading 30 |
| `hold` | holds the stance for `seconds` | stable at 0.434 m |

Every successful skill returns the body to the home stance height afterwards
(|bodyZ - 0.434| < 0.02), so paid actions can run back to back.

## Requirements

- Python 3.10+ (tested 3.12), `pip install mujoco>=3.1.3 numpy pybullet eclipse-zenoh`
- The MuJoCo model needs MuJoCo 3.1.3+ (menagerie requirement).
- No tunnel binary is needed for the tests: the payment gate is a faithful
  Python reimplementation of the tunnel's x402 decisions (see below), and the
  wire tests publish the exact `handlers.PostAction` event schema.

Developed and validated on Windows 11 (python 3.12); the same tests run on
ubuntu-latest via CI.

## Setup

```sh
cd simulation
./setup.sh   # fetch the official Spot model assets (pinned commit)
```

## Tests

Each test prints its checks as JSON and PASS/FAIL, and exits nonzero on
failure.

```sh
cd simulation/spot
python3 test_spot_control.py       # every skill's physics actually happen
python3 test_payment_gate.py       # x402 gate: 402/400/409, no-settle-on-failure
python3 test_result_semantics.py   # success/error results, replay, tampering
python3 test_link.py               # paid action -> Zenoh -> episode -> result
cd ../pybullet
python3 test_sim2sim.py            # same poses in MuJoCo and PyBullet, compared
```

`test_payment_gate.py` drives the gate directly (unpaid -> 402 +
PAYMENT-REQUIRED, expired/forged receipts -> 402, replayed idempotencyKey /
txHash -> 409, and a settlement ledger proving that only `"status":
"success"` results settle). `test_result_semantics.py` runs the full link
with peer-mode Zenoh and proves every failure path returns an error result.
`test_link.py` publishes one valid paid `wave` action and expects a success
result carrying the physics metrics (pawLift, bodyZ) correlated by actionId.

## Wire contract

Zenoh topics (peer mode; the link and the test harness discover each other on
localhost, no separate router needed):

| topic | direction | schema |
|---|---|---|
| `robot/tunnel/action` | tunnel -> robot | tunnel event: `{payload, transaction_details, timestamp}`; `payload` is the action envelope `{actionId, robotId, skillId, params, paramsHash, idempotencyKey, payment}` |
| `robot/tunnel/result` | robot -> relay | `{"status": "success", actionId, skill, result: {message, metrics}}` or `{"status": "error", actionId, skill, error: {code, message}}` |

The skill catalog lives in `spot/skills.json` (7 priced skills, $0.002 each;
printed at startup for discovery). `robopay_link.py` validates every
envelope: unknown skill, out-of-schema or tampered params (`paramsHash` is
sha256 of canonical JSON), wrong robotId, and replayed `idempotencyKey` all
produce an error result and never actuate the robot. Error codes:
`UNKNOWN_SKILL, INVALID_PARAMS, WRONG_ROBOT, REJECTED_PAYMENT, UNPAID,
DUPLICATE, ACTION_FAILED`. **The relay must settle only on
`"status": "success"`** — `test_result_semantics.py` proves every failure
path yields an error result (no-settle-on-failure evidence).

Note on the return path: the tunnel in this repo does not yet consume
execution results, so publishing them on the documented result topic is the
integration point this submission provides — the relay can subscribe there to
correlate by `actionId` and decide settlement.

Payment gate: `spot/payment_gate.py` reimplements the tunnel's x402
middleware so the simulator-only submission exercises the same semantics end
to end — receipts are Ed25519-signed by a local facilitator whose key is
persisted next to the module (so the payer side and the robot side share one
trusted facilitator, like the tunnel trusts the advertised facilitator
public key), a replayed idempotencyKey or txHash is a 409, and settlement is
recorded only for success results. No private keys or secrets leave the repo;
no on-chain settlement happens.

Configuration (env vars, defaults in parentheses):

- `ROBOPAY_ACTION_TOPIC` (`robot/tunnel/action`), `ROBOPAY_RESULT_TOPIC`
  (`robot/tunnel/result`), `ROBOPAY_ROBOT_ID` (`test-robot`, matching
  `tunnel/config.json`)
- `SPOT_MODEL_PATH` (default `models/mujoco_menagerie/boston_dynamics_spot/scene.xml`
  relative to `simulation/`)

### Robot identity, wallet binding and safety

- **Robot identity** — `ROBOPAY_ROBOT_ID` binds the robot to the payee
  wallet through the tunnel's `config.json` (`robot_id` +
  `evm_payee_address`). Every envelope is checked against `robotId`; a
  mismatch returns `WRONG_ROBOT` and never actuates the robot.
- **Safe stop** — the `stop` skill is the fail-safe action: it halts motion
  and returns the robot to the stable home stance on a short timeline. Any
  payer can request it at any time.
- **Testnet** — the profile's payment policy targets `eip155:84532` (Base
  Sepolia testnet); configure `network` and `token_address` in
  `tunnel/config.json` for the chain you settle on.
- **Security warning** — private keys must only be supplied through
  environment variables or a secret manager (e.g. the facilitator key file
  used by the simulator gate). Never hardcode, commit, or log private keys;
  the repo `.gitignore` excludes `.env`, `*.b64` and `keys/`. The simulator
  writes its facilitator key next to `payment_gate.py` on first run for
  local-only playback and should not be treated as a production secret.

A machine-readable robot profile (skills, payment policy, execution mapping,
example envelope, skill-contract tests, validation report) is in
`registry/vendors/boston_dynamics/spot/boston_dynamics.spot.mujoco-pybullet-sim.v1/`.

## Sim-to-sim results

`test_sim2sim.py` captures each skill's salient pose in MuJoCo (wave peak
lift, sit deepest crouch, bow max pitch, nod max dip, end of turn, home) and
recomputes the exact same joint configuration in PyBullet via the kinematic
URDF. Foot-tip positions agree to **0.06 cm** maximum across all poses and
all four feet (`pybullet/sim2sim_report.json`) — the two engines run the same
kinematics for every skill.

The kinematic URDF `pybullet/spot_simple_kin.urdf` is generated from the
rai-opensource/spot_description `spot_simple.urdf.xacro` (BSD-3-Clause) with
visual/collision meshes stripped; the retained joint frames, origins and
inertials are byte-identical to the menagerie MJCF (the same upstream
description), which is why the comparison is exact.

Expected outputs — success (test_link.py) and failure
(test_result_semantics.py) results on `robot/tunnel/result`:

```json
{"actionId": "act_…", "skill": "wave", "status": "success",
 "result": {"message": "Action completed",
            "metrics": {"pawLift": 0.212, "bodyZ": 0.432, "…": "…"}}}
{"actionId": "act_…", "skill": "turn_to_face", "status": "error",
 "error": {"code": "INVALID_PARAMS", "message": "'headingDeg' must be degrees with |v| <= 180.0"}}
```

## Troubleshooting

- **Tests hang waiting for Zenoh messages**: another process may hold a
  stale session. On Linux `pkill -f robopay_link.py`; on Windows kill the
  leftover `python` processes and retry.
- **MuJoCo fails to load the model**: the menagerie Spot needs MuJoCo 3.1.3+.
- **HTTPS blocked when fetching models**: `GIT_HOST=git@github.com: ./setup.sh`
  clones over SSH instead.

## Layout

```
simulation/
├── setup.sh                 fetch pinned official Spot model assets
├── spot/
│   ├── spot_control.py      joint-space skill controller on MuJoCo
│   ├── payment_gate.py      x402 gate (402/409, settle-only-on-success)
│   ├── robopay_link.py      action validation, payment gate, skill execution
│   ├── skills.json          priced skill catalog (discovery)
│   ├── simulate_paid_action.py
│   ├── test_spot_control.py / test_payment_gate.py / test_result_semantics.py / test_link.py
│   └── (dev-only _t*.py calibration scripts)
└── pybullet/
    ├── spot_simple_kin.urdf kinematic URDF (mesh-free) for sim-to-sim
    ├── test_sim2sim.py      MuJoCo poses recomputed in PyBullet, compared
    └── sim2sim_report.json
```
