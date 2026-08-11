# LimX TRON 2 Tier 1 — paid MuJoCo + Webots obstacle navigation

This simulator-only profile runs a fixed obstacle course with the official LimX
`WF_TRON2A` wheeled-foot model. MuJoCo loads LimX's MJCF and official Isaac Gym
ONNX policy directly. Webots converts the matching official URDF and eleven STL
meshes; it does not use a substitute robot.

## Pinned upstream identity

- [`limxdynamics/tron2-robot-description`](https://github.com/limxdynamics/tron2-robot-description), commit `682d513d03f7e3d2a59ae791d50adc5ccb84dd1a`, Apache-2.0. The former `limx-tron2/robot-description` URL now redirects here.
- [`limxdynamics/tron2_rl_deploy_python`](https://github.com/limxdynamics/tron2_rl_deploy_python), commit `16db4e19eb28664a101fed7135d20d6c7f52bd38`, Apache-2.0
- Variant: `WF_TRON2A`; URDF, MJCF, mesh and ONNX hashes are pinned in `robot.profile.yaml`

The profile does not commit the 35 MiB binary asset set. The setup helper
downloads the exact `tron2/WF_TRON2A` files from those two commits and rejects
any byte that does not match the declared SHA-256. The upstream `LICENSE`,
`NOTICE`, third-party notices and model card are retained under `vendor/`.
This profile makes no broader redistribution claim for the STL or ONNX files
than the status recorded by LimX in those pinned notices.

## What the action does

`navigate_obstacle_course` follows ten state-driven waypoints through a
three-obstacle slalom corridor and terminates only from measured simulator
state. MuJoCo uses the vendor policy to turn current base/joint observations
and velocity commands into torques, providing actuator-level validation. Webots performs
task-level Sim-to-Sim validation: the same online route planner reads the
converted vendor model's measured pose, velocity, orientation and contacts,
then sends bounded chassis-velocity commands (maximum `0.25 m/s`) to the
dynamic official model. It never writes root translation/rotation or replays a
trajectory. Success means all waypoints and the goal were reached,
all obstacles were detected and no obstacle contact occurred. `stop` is a
separate paid idle safe-stop: it confirms zero velocity without starting a
navigation episode. It does not cancel an already-running episode.

No caller-controlled motion parameters are accepted.

The fixed planner bounds the episode to 70 seconds, linear velocity to
`0.65 m/s` and yaw rate to `0.5 rad/s`. Those limits are profile-owned and
cannot be raised by a paid request.

## Reproduce locally

```powershell
cd registry/vendors/limx/tron2/limx.tron2.wheeled-mujoco-webots-obstacle-nav.v1
py -3 -m pip install -r bridge/requirements-dev.txt
py -3 bridge/download_vendor_assets.py
./run-tests.ps1
./run-visual-mujoco.ps1
./run-visual-webots.ps1
```

Set `WEBOTS_EXE` if Webots R2025a is not on `PATH`. The generated PROTO records
its vendor provenance and contains only repository-relative mesh paths.

### Zenoh session and message contract

For a standalone development session, start a Zenoh 1.9 router and point both
Tunnel and bridge at it:

```powershell
zenohd -l tcp/127.0.0.1:7447
$env:ZENOH_ENDPOINT = 'tcp/127.0.0.1:7447'
$env:PYTHONPATH = "$PWD/bridge"
py -3 -m limx_tron2_sim.bridge
```

The bridge subscribes to `robot/tunnel/action`, publishes terminal results to
`robot/tunnel/result`, metrics to `robot/limx_tron2/metrics`, and readiness to
`robot/limx_tron2/ready`. The bridge is the robot-control process: it validates
the correlated event, reserves it durably and invokes the real MuJoCo runtime.

The private input event preserves this tuple:

```json
{
  "action_id": "act-001",
  "robot_id": "limx-tron2-wf-sim-01",
  "skill_id": "navigate_obstacle_course",
  "idempotency_key": "act-001",
  "params_hash": "sha256:...",
  "payload": {"action": "navigate_obstacle_course", "params": {}},
  "transaction_details": {"payment_payload": {}, "payment_requirements": {}}
}
```

The result repeats `action_id`, `robot_id`, `skill_id`, `idempotency_key` and
`params_hash`, adds `status`, and places the measured simulator output under
`result`.

## Payment, authorization and replay contract

- Base Sepolia (`eip155:84532`), USDC, `$0.001` for either registered skill.
- The real Go Tunnel verifies x402 before `PostAction`. A nil or `isValid:false`
  facilitator response returns `402` and publishes zero ActionEvents.
- The Tunnel settles only after the correlated terminal result reports success.
  Failure, timeout and invalid payment remain unsettled.
- The durable JSON replay implementation protects the public Tunnel boundary.
  The profile additionally persists its private Zenoh
  execution reservation in SQLite, bound to action ID, idempotency key and
  payment fingerprint.
- Missing/unknown action, mismatched skill, foreign robot, changed parameters,
  uncorrelated Zenoh data and repeated payment evidence all fail closed.
- Robot WebSocket identity-to-payee signing remains an upstream shared
  Tunnel/Gateway dependency; this profile does not invent a local EIP protocol.

Runtime-only configuration:

| Variable | Purpose |
| --- | --- |
| `ROBOT_ID` | Must equal `limx-tron2-wf-sim-01` |
| `ROBO_PAYEE_ADDRESS` | Non-zero testnet payee wallet |
| `BASE_SEPOLIA_PRIVATE_KEY` | Test payer, visual runner only; never passed to the bridge |
| `SKILL_CATALOG_PATH` | Absolute path to `skill-catalog.json` |
| `ALLOWED_ACTIONS` | `navigate_obstacle_course,stop` |
| `ZENOH_CONFIG` or `ZENOH_ENDPOINT` | Explicit private Zenoh session |
| `TUNNEL_BIN` | Real catalog-aware Go Tunnel binary |

Never commit, print or record a private key. Use an untracked process
environment locally and GitHub Actions secrets in CI.

### Paid Base Sepolia visual proof

Build the real Linux Tunnel from the repository root (`make build`), start a
Zenoh 1.9 router on `tcp/127.0.0.1:7447`, then use a funded disposable Base
Sepolia payer from PowerShell:

```powershell
$env:TUNNEL_BIN = 'C:\path\to\RoboPay\bin\tunnel'
$env:ROBO_PAYEE_ADDRESS = '0xYourNonZeroPayee'
$env:BASE_SEPOLIA_PRIVATE_KEY = '<load from an untracked secret source>'
./run-live-base-sepolia-visual.ps1 -OpenBaseScan
```

The runner starts the bridge and Tunnel, waits for the explicit Zenoh ready
event, sends exactly one paid navigation action after a clean start, waits for
the correlated terminal result and prints the settlement transaction. It
removes all payer-key variables from the simulator bridge environment.

## CI and evidence

`.github/workflows/limx-tron2-tier1.yml` builds the submitted hardened Tunnel
and runs the adversarial `isValid:false` regression, failure/timeout
non-settlement, durable replay, real Zenoh, official-policy MuJoCo, real Webots
Sim-to-Sim, and a trusted push/workflow-dispatch Base Sepolia settlement job.
The live runner waits for an explicit bridge-ready event, sends the paid action
once after a clean start, assembles WebSocket continuation frames and uploads
the receipt/result JSON.

The live settlement job intentionally runs only on a trusted fork push to
`limx-tron2-tier-1` or by `workflow_dispatch`, because GitHub does not expose
fork secrets to the upstream `pull_request` event. Its expected skip on the
upstream PR is not a missing simulator test: all offline authorization,
MuJoCo, Webots and Sim-to-Sim gates still run on the PR.
The trusted fork must define repository secrets `BASE_SEPOLIA_PRIVATE_KEY` and
`ROBO_PAYEE_ADDRESS`; the workflow never prints either value.

Expected success is HTTP `202`, followed by a status document with
`state: succeeded`, `settled: true`, the same `action_id`, measured course
metrics and a Base Sepolia transaction hash. Unknown skills/parameters return
a fail-closed error before Zenoh; injected execution failure returns terminal
`failed` with `settled: false`; a repeated request returns HTTP `409`.

### Troubleshooting

- **Bridge never ready:** verify the router is running and both processes use
  the same explicit `ZENOH_CONFIG`/endpoint.
- **Webots produces no JSON:** use R2025a and install `libsndio7.0` (or the
  distribution's compatible `libsndio` package).
- **Tunnel returns 503:** configure both the catalog/allowlist and a writable
  durable idempotency store; corrupt state deliberately fails closed.
- **Payment returns 402:** confirm Base Sepolia, payee, USDC asset and payer
  funds. Do not bypass verification.
- **Model hash test fails:** restore the pinned vendor asset; do not hand-edit
  the official URDF, MJCF, STL, ONNX or controller-parameter files. Delete only
  the affected downloaded file and rerun `bridge/download_vendor_assets.py`.

See [the validation report](docs/validation-report.md) and
[evidence manifest](docs/evidence/evidence-manifest.yaml).
