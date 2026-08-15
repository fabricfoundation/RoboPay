# Unitree Go2 Tier 1 — MuJoCo + Webots

This bridge implements the simulator-only `navigate_obstacles` and `stop`
skills for `unitree.go2.mujoco-webots-obstacle-nav.v1`. It follows the same
payment and execution contract as the approved Spot profile: the Go Tunnel is
the payment boundary, Zenoh carries correlated actions/results, and x402
settlement is attempted only after a terminal simulator success.

## Official models

`download_go2_model.py` fetches exact upstream revisions recorded in
`models/model.lock.json`:

- MuJoCo: Unitree's `unitree_mujoco` Go2 MJCF at
  `ae6a8403e272733e9996ef59990880330496177f`.
- Webots: Unitree's `unitree_ros` Go2 URDF/DAE assets at
  `f3772ce54c56ef2d34c6aee8100bc768896c7d19`, converted locally by
  `urdf2webots` for Webots R2025a.

The downloaded models are ignored build inputs, not modified or committed
copies. Both are Unitree-published Go2 descriptions; this integration does not
substitute a generic quadruped.

## What is simulated

An online state-feedback planner drives a four-waypoint corridor between two
physical obstacles. At every tick it reads the simulated body pose, selects a
waypoint, calculates heading error, and generates a diagonal foot-space trot.
Success requires the measured goal distance to fall below 0.32 m with zero
physical obstacle contacts.

- MuJoCo solves the foot targets into the 12 official Go2 joints and applies
  clipped PD torque to the MJCF torque actuators.
- Webots solves the same targets and commands the 12 motors created from the
  official URDF. The controller measures the root pose and obstacle contacts
  from Webots.

Neither adapter writes the root translation or rotation. There is no animation,
pre-recorded trajectory, Supervisor base motion, or simulator-success mock.
The task-level policy ID, goal, route, gait parameters, start pose, and terminal
metrics are compared by `run_sim2sim_validation.py`.

## Run locally

```powershell
python -m pip install -r bridge/unitree/go2_mujoco_bridge/requirements.txt
python bridge/unitree/go2_mujoco_bridge/download_go2_model.py
$env:PYTHONPATH = "$PWD/bridge/unitree/go2_mujoco_bridge"
python bridge/unitree/go2_mujoco_bridge/run_obstacle_nav.py
python bridge/unitree/go2_mujoco_bridge/run_webots_validation.py
python bridge/unitree/go2_mujoco_bridge/run_sim2sim_validation.py
```

Use `run_obstacle_nav.py --viewer` or
`run_webots_validation.py --viewer` for graphical review. The viewer changes
only wall-clock playback/rendering, not the physics or controller.

## Zenoh and paid end-to-end run

The default topics are `robot/tunnel/action`, `robot/tunnel/result`,
`robot/unitree_go2/metrics`, and the live-run readiness signal
`robot/unitree_go2/ready`. Start a local router, the bridge, and the Tunnel from
separate terminals:

```bash
zenohd
PYTHONPATH=bridge/unitree/go2_mujoco_bridge \
  ROBOT_ID=go2-mujoco-sim-01 \
  python -m go2_mujoco_bridge.bridge
ALLOWED_ACTIONS=navigate_obstacles,stop \
  SKILL_CATALOG_PATH=registry/vendors/unitree/go2/unitree.go2.mujoco-webots-obstacle-nav.v1/skill-catalog.json \
  ./bin/tunnel --config tunnel/config.json
```

Then send the paid action with:

```bash
PRIVATE_KEY=... ROBO_PAYEE_ADDRESS=0x... \
  python bridge/unitree/go2_mujoco_bridge/pay_go2_obstacle_nav.py
```

The trusted live runner subscribes to `robot/unitree_go2/ready` before it
starts the bridge and refuses to send payment until the bridge announces that
its action subscription exists. The first paid action therefore needs no
warm-up action or timing sleep.

For an OBS-ready Windows recording with the native MuJoCo viewer and the real
Linux Tunnel in WSL, first build the Tunnel and load the funded **testnet-only**
credentials into the current PowerShell process. Then run:

```powershell
wsl.exe -d Ubuntu-22.04 -- make build
$env:PRIVATE_KEY = '<Base Sepolia test wallet key from your secret manager>'
$env:ROBO_PAYEE_ADDRESS = '<configured payee>'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./bridge/unitree/go2_mujoco_bridge/run_live_base_sepolia_visual.ps1
```

Use `-DryRun` to rehearse discovery and the unpaid `402` without signing or
submitting a payment. `-ExecutionPolicy Bypass` applies only to that child
PowerShell process and does not change the machine's permanent policy.
Use `-PauseAfter` while recording to keep the concise settlement summary and
transaction hash visible until Enter is pressed; credentials are removed from
the child process environment before that pause.

On Windows, the launcher automatically places the terminal in the left 45% of
the primary work area and moves the native MuJoCo viewer into the right 55%.
This keeps both the correlated payment/result stream and the complete course
readable throughout the recording. A ten-second countdown lets the operator
start capture before the unpaid request; pass `-NoAutoLayout` only when using a
manually prepared split-screen layout.

The launcher also refuses to proceed while TCP port `7447` is already in use.
This check runs before signing or submitting payment and prevents a stale
Zenoh router from accepting the Tunnel connection while the current Go2 bridge
waits on a different session.

The launcher starts an isolated local Zenoh router, displays Tunnel logs and
MuJoCo motion, briefly holds the terminal scene, writes the correlated evidence
JSON under `artifacts/`, and opens the settlement transaction in BaseScan. The
default five-second hold keeps execution and deferred settlement comfortably
inside the x402 authorization window. It never prints or persists the private
key.

Never commit, print, or paste a production private key. Use a test-only Base
Sepolia wallet and load `PRIVATE_KEY` and `ROBO_PAYEE_ADDRESS` from local
environment variables or GitHub Secrets. `PROXY_WS_URL`, `ROBOT_ID`,
`ZENOH_ENDPOINT`, `ZENOH_CONFIG`, all four Zenoh topic variables,
`SKILL_CATALOG_PATH`, `ALLOWED_ACTIONS`, and `IDEMPOTENCY_STORE_PATH` are
configuration, not source constants.

The ActionEvent crossing the Zenoh boundary preserves `action_id`, `robot_id`,
`skill_id`, `idempotency_key`, `params_hash`, verified payment evidence, and
the `{maxDurationSec, side, speedScale}` params. A ResultEvent echoes the same
correlation tuple and contains `status`, `profile_id`, and structured simulator
metrics. Expected success is `202 accepted` followed by a status document with
`state=success`, `settled=true`, transaction evidence, goal distance, path,
heading change, and zero obstacle contacts. Invalid payment returns `402`;
invalid params return a terminal failure; simulator timeout returns
`state=timeout, settled=false`.

## Payment and safety contract

- `ALLOWED_ACTIONS` and the registry skill catalog are mandatory and fail
  closed.
- Missing, malformed, unknown, wrong-robot, or facilitator-rejected payments
  cannot publish an ActionEvent.
- The Tunnel returns immediate `202` with `action_id`; the status resource
  later exposes the correlated terminal result.
- Payment-bound replay reservations are durable across Tunnel restart.
- Failure, timeout, replay, correlation mismatch, and safe-stopped navigation
  remain unsettled.
- `stop` commands the neutral 12-joint pose and zeros MuJoCo velocity; it never
  falls through to navigation.

The real-Tunnel negative suites are `tests/test_payment_gate.py` and
`tests/test_x402_no_settlement.py`. Their recording facilitator is a local
observable x402 endpoint used to inject `isValid:false`, failure, and timeout;
the request router, middleware, Tunnel binary, Zenoh boundary, persistence, and
settlement decision are production code.

## Troubleshooting

- `Official Go2 MJCF missing`: run `download_go2_model.py` from a networked
  clean checkout.
- Webots produces no result: set `WEBOTS_EXE`; on Linux install the Webots
  runtime libraries listed in `.github/workflows/unitree-go2-tier1.yml`.
- Tunnel fails closed at startup: set a non-empty allowlist and valid skill
  catalog; this is intentional.
- No Zenoh action: verify both processes use the same `ZENOH_CONFIG` or
  `ZENOH_ENDPOINT` and the documented topics.
- `409 REPLAY_DETECTED`: use a new payment and action/idempotency key; replay
  reservations intentionally survive process restart.

## Identity boundary

The profile supplies a stable `robot_id`; the deployment supplies the payee
wallet to the shared Tunnel through its runtime configuration.
The WebSocket identity-to-payee signing/binding protocol is owned by the shared
Fabric Tunnel/Gateway and is not re-invented inside this robot profile.
