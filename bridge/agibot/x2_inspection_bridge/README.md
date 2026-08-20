# AGIBot X2 Ultra Tier 1 bridge

This bridge executes `inspect_target_sequence` against AGIBot's official X2
Ultra v1.4 model in real MuJoCo and Webots R2025a. It is a simulator-only,
fixed-pelvis inspection station with both feet at the floor; it does not claim
walking, balance, ROS2 hardware control, or a physical-robot validation.

## Provenance

The downloader pins `AgibotTech/agibot_x2_urdf` commit
`77f43eb0904dae4c48ccd9154fee824f8ffd4d38` and verifies the exact upstream
blobs before either simulator starts:

- `X2-Ultra.xml`: `bfccc26654f15c7e8015e8a993bba253b00d3b6d67d4d5072a44e04de6a340f0`
- `X2-Ultra.urdf`: `728952c1e966683a0de7c6615d7fd3595ef595c5d0bc449a8df5742b24834328`
- `X2-Ultra_simple_collision.urdf`: `e6ce8a434d9d4a2fb53201be71a1d4b1158a5ca4f65c46a050bc78b629d841ac`

The upstream MulanPSL-2.0 license is copied into the ignored model cache. The
large third-party meshes and generated PROTOs are never committed. The native
viewer uses every original upstream vertex. A separate CI-only PROTO uses
visual derivatives capped at 6,000 faces per link so headless runs do not spend
minutes optimizing geometry; kinematics, inertias, joints, limits, controller,
and task state remain identical.

## What the task does

One shared policy commands the official waist, head, and arm joints through
three visibly distinct poses: left, center, and right. It reads simulator joint
positions on every tick and confirms a target only after maximum error remains
within 0.075 rad for 0.55 seconds. MuJoCo uses torque PD plus model-derived
gravity/Coriolis compensation. Webots uses position motors. The policy ID,
target order, tolerance, dwell, speed scale, and fixture declaration must match
exactly for Sim-to-Sim success.

The official model exposes 31 actuated joints. The fixture constrains only the
pelvis; articulated state remains dynamic and measured. `stop` commands the
neutral articulated pose, zeros MuJoCo joint velocity, and makes an interrupted
inspection fail without settlement.

## Clean setup and local simulation

```bash
python -m pip install -r bridge/agibot/x2_inspection_bridge/requirements.txt
python bridge/agibot/x2_inspection_bridge/download_x2_model.py
```

MuJoCo, with an optional native viewer:

```bash
PYTHONPATH=bridge/agibot/x2_inspection_bridge \
python bridge/agibot/x2_inspection_bridge/run_inspection.py \
  --json-output bridge/agibot/x2_inspection_bridge/artifacts/mujoco_result.json

PYTHONPATH=bridge/agibot/x2_inspection_bridge \
python bridge/agibot/x2_inspection_bridge/run_inspection.py \
  --viewer --viewer-target-hold-seconds 2 --viewer-hold-seconds 3
```

Native Windows Webots and paired Sim-to-Sim:

```powershell
$env:PYTHONPATH = (Resolve-Path 'bridge/agibot/x2_inspection_bridge').Path
python bridge/agibot/x2_inspection_bridge/build_webots_model.py
python bridge/agibot/x2_inspection_bridge/run_webots_validation.py --viewer
python bridge/agibot/x2_inspection_bridge/run_sim2sim_validation.py --timeout 300
```

The first Webots load may spend several minutes compiling the dense official
STL meshes. The mandatory runner therefore uses a cold-cache-safe timeout and
fails if no controller result is produced.

## Paid action path

```text
unpaid/paid HTTP action -> Fabric Gateway WebSocket -> real Go Tunnel
  -> synchronous x402 verification -> Zenoh robot/tunnel/action
  -> X2 bridge -> live simulator episode -> correlated robot/tunnel/result
  -> Tunnel status store -> deferred settlement only after exact success
```

The bridge never receives a payment private key. The Tunnel must reject missing
or `isValid: false` verification before `PostAction`. Required tests assert HTTP
402, zero ActionEvents, zero simulator commands, and zero settlement calls. The
Tunnel durably reserves both idempotency key and payment fingerprint before
publication; replays remain HTTP 409 after restart.

Topics:

| Purpose | Zenoh key |
| --- | --- |
| verified actions | `robot/tunnel/action` |
| correlated results | `robot/tunnel/result` |
| X2 simulator metrics | `robot/agibot_x2/metrics` |

Result correlation includes `action_id`, `robot_id`, `skill_id`,
`idempotency_key`, and `params_hash`. A mismatched, failed, timed-out, stopped,
or replayed action never settles.

For a manual local bridge session, start a Zenoh router and point both the
Tunnel and bridge at it:

```bash
zenohd -l tcp/0.0.0.0:7447
export ZENOH_ENDPOINT=tcp/127.0.0.1:7447
export ROBOT_ID=agibot-x2-sim-01
export PYTHONPATH=bridge/agibot/x2_inspection_bridge
python -m x2_inspection_bridge.bridge
```

The bridge subscribes before writing its optional `AGIBOT_X2_READY_FILE`, so a
test or operator can send the first paid action without a warm-up action.

Successful results use `status: success` and include the simulator result:

```json
{
  "action_id": "x2-demo-001",
  "robot_id": "agibot-x2-sim-01",
  "skill_id": "inspect_target_sequence",
  "idempotency_key": "x2-demo-001",
  "params_hash": "sha256:<hex>",
  "status": "success",
  "result": {"success": true, "targets_confirmed": ["left", "center", "right"]}
}
```

Failures preserve the same tuple and use `status: failure`, for example
`result.error_code: INVALID_TARGETS`, `SIMULATOR_EXECUTION_ERROR`, or a
`safe_stopped` completion. The status endpoint additionally reports `failed`,
`timeout`, or `settlement_failed`; none of those states is settled.

## Contract and payment regression tests

```bash
PYTHONPATH=bridge/agibot/x2_inspection_bridge \
python -m unittest \
  bridge/agibot/x2_inspection_bridge/tests/test_policy.py \
  bridge/agibot/x2_inspection_bridge/tests/test_bridge_contract.py

python bridge/agibot/x2_inspection_bridge/tests/test_payment_gate.py
python bridge/agibot/x2_inspection_bridge/tests/test_x402_no_settlement.py
python scripts/registry/validate_profiles.py --registry-root registry/vendors/agibot
```

The two payment scripts require the real Linux Tunnel binary and Zenoh C
runtime. They are mandatory CI gates, not mock substitutes for the Tunnel.

## Base Sepolia cold-start evidence

CI runs the live action only on trusted `push` or manual `workflow_dispatch`
events because pull requests do not receive repository payment secrets. It
uses the real Fabric Gateway and real Tunnel, waits for the Zenoh subscriber
readiness file, sends unpaid HTTP 402, and then sends the first paid action with
no warm-up action.

Required environment variables are `PRIVATE_KEY` (or
`BASE_SEPOLIA_PRIVATE_KEY`) and `ROBO_PAYEE_ADDRESS`. Do not put either value in
the repository, command line, log, or evidence artifact.

```powershell
./bridge/agibot/x2_inspection_bridge/run_live_base_sepolia_visual.ps1 `
  -TargetHoldSeconds 2 -FinalHoldSeconds 3 -ViewerStartSeconds 8 -PauseAfter
```

The launcher displays the exact commit, pauses for Enter before payment, keeps
the MuJoCo viewer and terminal readable, opens the matching BaseScan receipt,
and writes `artifacts/base_sepolia_result_*.json`. Bind the commit, action ID,
transaction hash, recording SHA-256, attachment URL, and reviewed JSON artifact
in the evidence manifest only after a successful recording.

## Troubleshooting

- **Official assets not found or hash mismatch:** rerun
  `download_x2_model.py`. Do not substitute a different X2 or humanoid mesh.
- **The full Webots viewer takes time to open:** it intentionally loads all
  upstream vertices. Automated validation uses the separate CI-only visual
  derivative and should finish quickly.
- **Webots is not found on Windows:** set `WEBOTS_EXE` to
  `.../Webots/msys64/mingw64/bin/webots.exe`.
- **Tunnel binary missing during recording:** build it once with `make build`
  in Ubuntu 22.04/WSL. The PowerShell launcher then runs that binary through
  WSL while the Python signer, Zenoh router, bridge, and MuJoCo viewer remain
  native on Windows; the payer key is not forwarded to the Tunnel process.
- **Paid request remains HTTP 402:** verify the funded Base Sepolia payer,
  payee address, facilitator URL, network `eip155:84532`, and the
  `PAYMENT-REQUIRED` challenge. Never print the private key.
- **Action succeeds but settlement fails:** retain the JSON as failed evidence,
  check facilitator/USDC state, and rerun with a new action and payment. Never
  relabel `settlement_failed` as success.
