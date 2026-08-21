# Boston Dynamics Atlas DRC legacy — paid MuJoCo + Webots wave

This is a **Tier 1, simulator-only** profile for the DARPA-era Atlas DRC/v4
model. It is deliberately not described as Boston Dynamics' current electric
Atlas. The profile uses the same payment-gated Go Tunnel, x402, durable replay
state, and Zenoh result correlation boundary as the Reachy Mini and Spot
profiles.

## Model provenance and visual fidelity

The pinned source is OpenAI Roboschool commit `d32bcb2b35b94168b5ce27233ca62f3c8678886f`:
`atlas_v4_with_multisense.urdf`, its original Atlas DRC `.dae` meshes, and the
MultiSense head mesh. `download_atlas_model.py` verifies their locked SHA-256
values before use.

MuJoCo does not read DAE visual meshes directly. For an opt-in desktop view,
the bridge converts the **same checked-source visual triangles** locally to
OBJ, loads 23 display meshes, and disables collision on those display-only
geometries. Physics, joint names, limits, and the bounded torque controller
continue to come from the pinned original URDF. Generated source assets and
converted files are ignored by Git.

Webots R2025a uses the official externally referenced Atlas PROTO at the
pinned `R2025a` source tag; it is not vendored because its asset license is
for Webots use. The world defines a floor, background, directional light, and
the official Atlas camera framing.

## Electric Atlas compatibility boundary

As checked on 2026-08-21, Boston Dynamics' public electric Atlas product page
and product announcement specify **56 degrees of freedom** and continuous or
fully rotational joints, but do not publish a URDF, USD, joint names, axes,
limits, inertias, or actuator model:

- https://bostondynamics.com/products/atlas/
- https://bostondynamics.com/blog/boston-dynamics-unveils-new-atlas-robot-to-revolutionize-industry/
- https://bostondynamics.com/wp-content/uploads/2026/01/atlas-spec-sheet.pdf

NVIDIA's public Isaac Sim 5.1 robot-asset catalog lists Spot and Spot with arm
under `BostonDynamics`, but contains no Atlas asset:

- https://docs.isaacsim.omniverse.nvidia.com/5.1.0/assets/usd_assets_robots.html

The pinned DRC/v4 URDF contains 30 movable, single-axis joints. Human-level
concepts such as shoulder, elbow, hip and knee exist on both generations, but
that is not enough to establish a kinematic mapping. The electric model's 56
DoF, continuous range and unpublished topology mean DRC joint names, axes,
limits, torque gains and policies are **not treated as compatible or directly
transferable**. This profile is consequently named and scoped to
`atlas-drc-v4-legacy`; it is not a proxy claim for electric Atlas.

## Clean setup

```bash
make build
python -m pip install -r bridge/boston_dynamics/atlas_drc_bridge/requirements.txt
python bridge/boston_dynamics/atlas_drc_bridge/download_atlas_model.py
export PYTHONPATH="$PWD/bridge/boston_dynamics/atlas_drc_bridge"
```

On Windows PowerShell, replace `export` with `$env:PYTHONPATH = ...`.

## Visual simulator runs

Open the original-mesh MuJoCo view in a desktop session:

```bash
python bridge/boston_dynamics/atlas_drc_bridge/run_paid_wave.py --viewer \
  --cycles 2 --amplitude-rad 0.30 --max-duration 8 --viewer-hold-seconds 20
```

This is a local controller preview; it is useful for inspecting model geometry
and bounded state-feedback motion. The result still exposes measured joint
stroke, completed half-waves, finite state, and torque peak.

Run the independently supplied, illuminated Webots scene:

```bash
WEBOTS_EXE=/path/to/webots \
python bridge/boston_dynamics/atlas_drc_bridge/run_sim2sim_validation.py
```

For an operator recording, set `ATLAS_WEBOTS_RECORDING_PATH` to an absolute
MP4 path and run Webots in a graphical desktop session. Do not treat a
headless/offscreen capture as visual evidence without inspecting it.

## Tunnel, Zenoh, and action contract

The bridge refuses an implicit Zenoh session. Configure a private, authenticated
or otherwise isolated Tunnel-to-bridge boundary with `ZENOH_CONFIG`; the test
only `ZENOH_ENDPOINT` mode is for a controlled local test router. The topics
are:

| Direction | Topic |
| --- | --- |
| verified action from Tunnel | `robot/tunnel/action` |
| correlated terminal result | `robot/tunnel/result` |
| reviewable Atlas metrics | `robot/boston_dynamics_atlas_drc/metrics` |
| bridge readiness after subscription | `robot/boston_dynamics_atlas_drc/ready` |

The Tunnel requires these deployment values:

```bash
export SKILL_CATALOG_PATH="$PWD/registry/vendors/boston-dynamics/atlas/boston-dynamics.atlas-drc.mujoco-webots-wave.v1/skill-catalog.json"
export ALLOWED_ACTIONS="wave_right_arm,stop"
export IDEMPOTENCY_STORE_PATH=/secure/persistent/atlas-idempotency.json
export ZENOH_CONFIG=/secure/config/atlas-zenoh.json5
```

`robot_id`, testnet payee, price, and network are deployment configuration,
not profile constants. Keep private keys only with the payer/live-evidence
runner; the Tunnel and bridge do not need or retain one.

The Fabric gateway's `POST /robots/{robotId}/action` is forwarded to the
Tunnel's local `POST /action`. The Tunnel accepts only the registered tuple
`(robot_id, action == skill_id, params, action_id, idempotency_key)`. The wave
parameters are `cycles: 1..3`, `amplitudeRad: 0.15..0.40`, and
`maxDurationSec: 5..15`; unknown fields and actions fail closed before the
simulator. An accepted request returns immediate `202` with `action_id` and a
status URL. The same `action_id`, `robot_id`, `skill_id`, `params_hash`, and
`idempotency_key` are required on the terminal Zenoh result.

The bridge executes a measured-state, turning-point right-arm policy in
MuJoCo. It switches only after measured shoulder state crosses the bounded
turning point, returns to neutral before success, and never writes `qpos` to
fake a result. The Webots controller observes the corresponding actual
`RArmUsy` HingeJoint position through the Supervisor API.

## Payment and safety behavior

- Missing, malformed, nil, or `isValid:false` x402 verification fails before
  `PostAction`: HTTP `402`, zero ActionEvents, zero simulation output, and zero
  settlement.
- A paid wave settles only after the exact correlated simulator success.
- Simulator failure, timeout, malformed result, payment replay, action replay,
  and restart replay do not settle.
- `stop` has no parameters. It interrupts the active wave with zero torque and
  zero simulated velocity; that interrupted wave reports failure and cannot
  settle. A separately paid stop request can settle only for its **own**
  correlated safe-stop success result.
- The current shared Tunnel/Gateway identity protocol identifies the configured
  robot and payee but does not provide a signed robot-to-payee handshake. That
  protocol binding remains an explicit upstream dependency; this profile does
  not invent a local EIP signing scheme.

For a live Base Sepolia recording, use the trusted-fork secret-backed workflow
or run `test_base_sepolia_tunnel_e2e.py` with a funded payer. Set
`ATLAS_MUJOCO_VIEWER=1` before that script to make the actual paid bridge open
the MuJoCo viewer during its first paid action in a desktop-capable session.
The script verifies unpaid `402`, skill discovery, first paid `202`, correlated
simulator success, settlement, and writes the real transaction hash to an
artifact. Never commit keys, raw local logs, or unverified recordings.

On Windows, the reviewable split-screen runner keeps MuJoCo native and runs
the production Linux Tunnel in the `Ubuntu-22.04` WSL distribution:

```powershell
$env:PRIVATE_KEY = '<funded Base Sepolia test-wallet key>'
$env:ROBO_PAYEE_ADDRESS = '<payee address>'
& bridge\boston_dynamics\atlas_drc_bridge\run_live_base_sepolia_visual.ps1
```

The runner waits for an explicit bridge-ready event, shows the exact commit,
pauses at Enter before any paid request, then displays discovery, unpaid 402,
first paid 202/action ID, the complete measured wave, correlated result,
settlement and BaseScan. It writes the trusted JSON receipt under `artifacts/`;
copy only the verified receipt into `docs/evidence/` and bind its hash together
with the recording hash in `docs/evidence/evidence-manifest.yaml`.

## Mandatory checks

```bash
python bridge/boston_dynamics/atlas_drc_bridge/tests/test_contract.py
python bridge/boston_dynamics/atlas_drc_bridge/tests/test_registry_contract.py
python bridge/boston_dynamics/atlas_drc_bridge/tests/test_bridge_contract.py
python bridge/boston_dynamics/atlas_drc_bridge/tests/test_payment_gate.py
python bridge/boston_dynamics/atlas_drc_bridge/tests/test_e2e_paid_action.py
python bridge/boston_dynamics/atlas_drc_bridge/tests/test_x402_no_settlement.py
python bridge/boston_dynamics/atlas_drc_bridge/tests/test_mujoco_runtime.py
```

The GitHub workflow makes the first seven tests, MuJoCo proof, and Webots
Sim-to-Sim proof required. Its trusted-fork Base Sepolia job waits for all of
them and uploads the generated receipt/result JSON.

## Troubleshooting

- If the Atlas falls in Webots, confirm the submitted world still uses the
  official `translation 0 0 1`, `CFM 1e-07`, `ERP 0.8`, and 8 ms basic time
  step. The result must report `stable_base=true`; arm stroke alone is not a
  passing result.
- If Webots fails on Windows with a Qt `offscreen` plugin error, do not export
  `QT_QPA_PLATFORM=offscreen`; the launcher removes that CI-only setting on
  Windows.
- If the visual runner reports port 7447 in use, stop the stale Zenoh router.
  The runner intentionally refuses to pay into an unknown local session.
- If the Tunnel binary is missing, run `make build` inside `Ubuntu-22.04` WSL.
- A Base Sepolia `402 invalid_exact_evm_signature` is not a simulator failure;
  verify that the payer key is funded, the payee matches discovery, and the
  x402 Python/Go versions match the pinned requirements before retrying.
