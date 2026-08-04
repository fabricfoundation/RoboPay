# Boston Dynamics Spot — Tier 1 obstacle navigation

This is a simulator-only RoboPay profile for Boston Dynamics Spot. Its paid
`navigate_obstacle_course` skill uses a pose-feedback planner and a diagonal
gait controller to steer the real free-base Spot MJCF around an obstacle. It
does not set the robot's base pose or replay an animation. The Webots runner is
included for real cross-engine validation; its current local result is recorded
honestly in the profile validation report.

## Model and prerequisites

The MuJoCo model is the BSD-3-Clause Spot MJCF from MuJoCo Menagerie, pinned to
commit `71f066ad0be9cd271f7ed58c030243ef157af9f4`.  It is deliberately a local,
ignored download because its third-party mesh assets are approximately 57 MB.

```powershell
python bridge/boston_dynamics/spot_mujoco_bridge/download_spot_model.py
python -m pip install -r bridge/boston_dynamics/spot_mujoco_bridge/requirements.txt
```

For an existing model download, set `SPOT_MJCF_DIR` to its directory.  The
cross-engine run needs Webots R2025a; set `WEBOTS_EXE` only when it is not
discoverable automatically.

## Run

```powershell
python bridge/boston_dynamics/spot_mujoco_bridge/run_obstacle_course.py --json-output bridge/boston_dynamics/spot_mujoco_bridge/artifacts/mujoco_result.json
python bridge/boston_dynamics/spot_mujoco_bridge/run_webots_validation.py --json-output bridge/boston_dynamics/spot_mujoco_bridge/artifacts/webots_result.json
python bridge/boston_dynamics/spot_mujoco_bridge/run_sim2sim_validation.py
# Open the interactive, real-time Webots view (it pauses on the final state).
python bridge/boston_dynamics/spot_mujoco_bridge/run_webots_validation.py --viewer
```

The result includes final goal distance, path length, minimum obstacle
clearance, actual obstacle contacts, control steps, and success/failure.  A
Webots failure is reported as a failure and is never replaced by a MuJoCo mock.

## Sim-to-sim scope

`run_sim2sim_validation.py` executes both engines and fails unless their
`policy_id`, start pose, goal, reference route, gait frequency, stabilization
period, and steering parameters are identical.  The shared policy is
`spot-obstacle-policy-v2-shared` and closes the loop from each engine's
measured pose.

The visible joint motion and path will not be frame-for-frame identical: the
Menagerie MJCF and Cyberbotics PROTO have different joint-zero conventions and
physics.  Each has a small actuator adapter that converts the same shared gait
and steering command to its own joints; it does not change the route or choose
a separate action policy.

## x402 settlement and no-settlement tests

The local integration tests use the real Go Tunnel, real x402 middleware, a
protocol-accurate local Fabric proxy, and a recording facilitator. They do not
use a private key or submit a transaction:

```bash
# Build the Tunnel first (Linux/WSL is required for the Zenoh C dependency).
make build
export TUNNEL_BIN="$PWD/bin/tunnel"
export LD_LIBRARY_PATH="$PWD/.zenoh-c/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

# Unpaid and malformed requests must receive 402 before any action is published.
python bridge/boston_dynamics/spot_mujoco_bridge/tests/test_payment_gate.py

# Direct parsing, routing, success/failure, limits, and safe-stop proof.
python bridge/boston_dynamics/spot_mujoco_bridge/tests/test_bridge_contract.py

# Injected Spot failure, timeout and durable replay must make zero /settle calls.
python bridge/boston_dynamics/spot_mujoco_bridge/tests/test_x402_no_settlement.py
```

The live Base Sepolia test is the positive settlement proof. It sends one real
testnet USDC authorization only after it has received 402, waits for the
correlated robot result, and succeeds only when the terminal action status is
`succeeded`, `settled: true`, and includes the facilitator transaction hash.
The `spot-base-sepolia-e2e` GitHub Actions job is manual
(`workflow_dispatch`) so ordinary pushes never spend testnet funds. When a
maintainer explicitly runs it with the two repository secrets, its log and
uploaded artifact show `SETTLED`, the observed time, the transaction hash, and
its BaseScan link.

Run it from an Ubuntu/WSL environment where the Tunnel binary has been built:

```bash
export PRIVATE_KEY=0x...                 # funded Base Sepolia test key; never commit
export ROBO_PAYEE_ADDRESS=0x...          # Base Sepolia test payee
python bridge/boston_dynamics/spot_mujoco_bridge/test_base_sepolia_tunnel_e2e.py
```

The test creates a temporary robot ID and writes only public settlement
evidence to `artifacts/base_sepolia_result_*.json`; it never writes the payer
private key or payment authorization to disk.

## Paid bridge

`spot_mujoco_bridge.bridge.SpotZenohBridge` subscribes to
`robot/tunnel/action`, validates incoming actions against the published Spot
profile, and publishes a correlated result to `robot/tunnel/result` and
`robot/boston_dynamics_spot/metrics`. Unknown actions, invalid duration,
invalid speed, and invalid routing-side values fail before simulator actuation.
The `stop` skill interrupts an active worker, applies neutral controls with
zero simulator velocity, and produces correlated stop/navigation outcomes.

Deployment values are configurable without source changes:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROBOT_ID` | `spot-mujoco-sim-01` | Exact robot identity required on every Tunnel-verified action event. |
| `ZENOH_CONFIG` | unset | JSON5 Zenoh configuration; takes precedence over `ZENOH_ENDPOINT`. |
| `ZENOH_ENDPOINT` | Zenoh discovery | Direct client endpoint such as `tcp/127.0.0.1:7447`. |
| `ZENOH_ACTION_TOPIC` | `robot/tunnel/action` | Paid action subscription. |
| `ZENOH_RESULT_TOPIC` | `robot/tunnel/result` | Correlated terminal result publication. |
| `ZENOH_METRICS_TOPIC` | `robot/boston_dynamics_spot/metrics` | Simulator metrics publication. |

Use the same action/result topic values in the Tunnel. Set
`SKILL_CATALOG_PATH` to this profile's `skill-catalog.json` and
`ALLOWED_ACTIONS=navigate_obstacle_course,stop`; the Tunnel fails closed if
either is absent. Its relay URL, robot identity, payment policy, and topics are
documented in `tunnel/.env.example`.

The profile in `registry/vendors/boston-dynamics/spot/` defines the exact
action schema, x402 policy, and execution mapping.  As with the existing
RoboPay tunnel, a failed simulator result must not settle payment.

## Record a paid MuJoCo demo

The graphical recording path is still the real paid path: Fabric Gateway → Go
Tunnel → `robot/tunnel/action` → this bridge → MuJoCo →
`robot/tunnel/result` → execution-gated settlement. It is not a local
animation or a manually triggered viewer.

Start a Zenoh router and the Tunnel in WSL, using a WSL Zenoh client config
that connects to `tcp/127.0.0.1:7447`. The Tunnel must have
`ALLOWED_ACTIONS=navigate_obstacle_course,stop`,
`PROXY_WS_URL=wss://api.fabric.foundation/api/core/ws/robot`, and a durable
`IDEMPOTENCY_STORE_PATH` under `artifacts/`.

On Windows, point a Zenoh client config at the current WSL IP and start this
bridge with the native viewer opt-in:

```powershell
$env:PYTHONPATH = "$PWD\bridge\boston_dynamics\spot_mujoco_bridge"
$env:ZENOH_CONFIG = "$PWD\bridge\boston_dynamics\spot_mujoco_bridge\artifacts\zenoh-windows.json5"
$env:SPOT_MUJOCO_VIEWER = '1'
$env:SPOT_MUJOCO_VIEWER_HOLD_SECONDS = '5'
python -m spot_mujoco_bridge.bridge
```

Then, in another Windows PowerShell, run the operator payer. It requests a
test private key through a hidden terminal prompt, performs the required
unsigned 402 quote check, then signs and polls the action to settled success:

```powershell
$env:ROBOT_ID = 'spot-mujoco-sim-01'
$env:ROBO_PAYEE_ADDRESS = '0x...your-testnet-payee...'
python bridge\boston_dynamics\spot_mujoco_bridge\pay_spot_obstacle_course.py --prompt-for-private-key
```

The model, Zenoh configs, replay state, logs, and raw recordings belong under
ignored `artifacts/` paths. The reviewed recording in the profile's
`docs/evidence/` directory is the sole versioned demo asset. Do not commit a
key, payment authorization, or a recording containing credentials. Omit
`SPOT_MUJOCO_VIEWER` for headless CI.

## Robot identity boundary

The current shared Gateway/Tunnel protocol identifies robots with `?id=` and
does not provide a signed robot-to-payee handshake. Per maintainer guidance,
that is documented as an upstream protocol dependency instead of introducing
EIP-191 in this robot bridge. Deployment configuration binds `ROBOT_ID` and
`ROBO_PAYEE_ADDRESS`; the bridge receives neither wallet private keys nor
payment authorization secrets.
