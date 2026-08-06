# DeepRobotics Lynx M20 Pro — payment-gated MuJoCo + Webots profile

This is a **Tier 1, simulator-only** RoboPay profile for the DeepRobotics
Lynx M20 Pro wheeled-legged base. It uses the hardened Go Tunnel/x402 payment
boundary, durable payment-bound replay state, Zenoh action/result correlation,
and an actual physics run before settlement.

## Model provenance and scope

The robot model is pinned from the vendor's BSD-3 licensed
[`DeepRoboticsLab/deep_robotics_model`](https://github.com/DeepRoboticsLab/deep_robotics_model)
repository at commit `9f8b97ca57e79f2e82771cf934c4fc975ee2887c`.
`download_m20_model.py` fetches only its `M20` tree and verifies the locked
MJCF, URDF, and representative binary mesh checksums in `models/model.lock.json`.

The vendor repository calls the shared mechanical model **M20**. It is an
appropriate 16-DOF wheeled-legged kinematic base for Lynx M20 Pro simulation,
but it does not claim to model the Pro-specific cameras, LiDAR, compute stack,
or optional payloads. MuJoCo reads that vendor MJCF directly. Webots R2025a
uses a PROTO generated at run time from the same locked vendor URDF; it is not
represented as a vendor-supplied Webots asset.

## Clean setup

```bash
make build
python -m pip install -r bridge/deep_robotics/m20_pro_mujoco_bridge/requirements.txt
python bridge/deep_robotics/m20_pro_mujoco_bridge/download_m20_model.py
export PYTHONPATH="$PWD/bridge/deep_robotics/m20_pro_mujoco_bridge"
```

On Windows PowerShell, set `PYTHONPATH` with
`$env:PYTHONPATH = "$PWD/bridge/deep_robotics/m20_pro_mujoco_bridge"`.

## Visual simulator runs

Open the original vendor MJCF in MuJoCo with the dynamic obstacle-yield
controller:

```bash
python bridge/deep_robotics/m20_pro_mujoco_bridge/run_obstacle_course.py --viewer --viewer-hold-seconds 60
```

This is real free-base physics: a red physical course obstacle blocks the
route. Like the Spot profile, the controller uses measured simulator base pose
and the profile-owned course geometry to determine clearance, applies zero
wheel command to yield, waits for the external obstacle actor to clear
laterally, and then resumes. It does not emulate a LiDAR/camera or claim that
the vendor M20 MJCF contains the M20 Pro sensors. The controller writes only
bounded leg/wheel motor controls; it never writes a base pose or joint state to
fake a result. Success requires measured clearance, yield, release,
collision-free state, goal displacement, height, tilt and finite simulator
state.

To inspect both simulators, including the illuminated Webots scene, run:

```bash
WEBOTS_EXE=/path/to/webots \
python bridge/deep_robotics/m20_pro_mujoco_bridge/run_sim2sim_validation.py --viewer
```

The scene has a floor, background, directional light, and camera. For CI use
the same command without `--viewer`; Webots is launched headlessly but still
executes its own Supervisor controller and returns measured state.

## Tunnel, Zenoh, and action contract

The bridge is profile-scoped to `lynx-m20-pro-sim-01` and refuses an implicit
Zenoh session. Production must use a private authenticated/isolated
Tunnel-to-bridge boundary through `ZENOH_CONFIG`; the `ZENOH_ENDPOINT` option
exists only for controlled local tests. The protocol topics are:

| Direction | Topic |
| --- | --- |
| Tunnel-verified action | `robot/tunnel/action` |
| correlated terminal result | `robot/tunnel/result` |
| reviewable M20 metrics | `robot/deep_robotics_m20/metrics` |

Configure the Tunnel with the profile artifacts and a durable storage path:

```bash
export SKILL_CATALOG_PATH="$PWD/registry/vendors/deep-robotics/lynx-m20-pro/deep-robotics.lynx-m20-pro.mujoco-webots-obstacle-nav.v1/skill-catalog.json"
export ALLOWED_ACTIONS="navigate_obstacle_course,stop"
export IDEMPOTENCY_STORE_PATH=/secure/persistent/m20-idempotency.json
export ZENOH_CONFIG=/secure/config/m20-zenoh.json5
```

`robot_id`, payee address, price, and network come from the deployed Tunnel
configuration. The bridge additionally rejects any identity other than the
registered profile identity. The shared Tunnel/Gateway protocol remains the
authority for robot-to-payee identity binding; this profile deliberately does
not invent an unreviewed local EIP signing scheme.

`navigate_obstacle_course` accepts only the registered tuple:

```json
{
  "robot_id": "lynx-m20-pro-sim-01",
  "action": "navigate_obstacle_course",
  "skill_id": "navigate_obstacle_course",
  "params": {"goalDistanceM": 1.35, "wheelSpeedRadS": 4.0, "maxDurationSec": 16}
}
```

Its bounded parameters are `goalDistanceM: 1.25..1.55`,
`wheelSpeedRadS: 2..6`, and `maxDurationSec: 12..20`. The model's stock wheel
contact does not expose a credible steering joint, so this profile truthfully
implements obstacle **avoidance by safe yield and resume**, rather than
pretending it can sidestep by overwriting pose. Unknown/missing actions,
skills, parameters, and identity mismatches fail closed before motion. `stop`
is parameterless and commands a safe zero-control stop; it never falls through
to navigation behavior.

The HTTP action response is immediate `202` with `action_id` and `state:
pending`; the caller polls its correlated status endpoint for a terminal
result. The terminal Zenoh result must retain `action_id`, `robot_id`,
`skill_id`, `params_hash`, and `idempotency_key`.

## Payment and safety guarantees

- A missing, malformed, nil, or facilitator-rejected (`isValid:false`) x402
  payment is rejected with HTTP `402` **before** `PostAction`: zero
  ActionEvents, simulator executions, result/metrics publications, and
  settlement calls.
- Only an exact correlated simulator `success` can trigger settlement. A
  positive action is never settled on acceptance alone.
- Failure, timeout, malformed result, payment replay, action replay, and
  replay after Tunnel restart remain unsettled and cannot create a second
  simulator action.
- A paid `stop` settles only for its own correlated safe-stop success. The
  interrupted drive reports failure and remains unsettled.

The mandatory local protocol tests use a controlled Fabric-compatible proxy
and recording facilitator (no wallet or public chain); they do **not** replace
the Go Tunnel/x402 middleware, WebSocket reader, Zenoh transport, or M20
bridge. A trusted-fork workflow below performs separately a live Base Sepolia
payment and writes the generated receipt/result JSON as an artifact.

## Mandatory checks

```bash
python bridge/deep_robotics/m20_pro_mujoco_bridge/tests/test_contract.py
python bridge/deep_robotics/m20_pro_mujoco_bridge/tests/test_registry_contract.py
python bridge/deep_robotics/m20_pro_mujoco_bridge/tests/test_bridge_contract.py
python bridge/deep_robotics/m20_pro_mujoco_bridge/tests/test_payment_gate.py
python bridge/deep_robotics/m20_pro_mujoco_bridge/tests/test_e2e_paid_action.py
python bridge/deep_robotics/m20_pro_mujoco_bridge/tests/test_x402_no_settlement.py
python bridge/deep_robotics/m20_pro_mujoco_bridge/tests/test_mujoco_runtime.py
WEBOTS_EXE=/path/to/webots python bridge/deep_robotics/m20_pro_mujoco_bridge/run_sim2sim_validation.py
```

The profile workflow runs these contract, real Tunnel, real Zenoh, MuJoCo and
Webots checks as mandatory workflow steps. Its Base Sepolia evidence job runs
only on a trusted push or explicit workflow dispatch after those gates and
consumes repository secrets; it is not skipped on a trusted run. Repository
branch protection can mark this workflow as a merge-required status check.
