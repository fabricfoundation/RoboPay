# Booster K1 Tier 1 active-inspection bridge

This profile executes a paid, non-prerecorded inspection task on Booster
Robotics' official 22-DoF K1 model in MuJoCo and Webots. It follows the same
Tunnel → x402 → Zenoh → simulator → correlated result boundary as the approved
Spot profile, while keeping K1-specific control and evidence independent.

## Honest simulator scope

Booster publishes the full K1 MJCF/URDF but no general K1 walking policy in its
public locomotion packages. This profile therefore does **not** claim K1
locomotion and never translates the free base. The robot is mounted in an
explicit fixed-base safety stand in both engines, with both feet visibly
resting on the floor. The embodied task moves the real head and arm joints
through the official actuators. A shared feedback
policy advances among left, center, and right targets only after measured joint
error remains below 0.09 rad for the configured dwell interval.

The pinned source is `BoosterRobotics/booster_assets` commit
`508cbee6ca9ae6fbc8c0b38dd58785a6f3fc61a2` (BSD-3-Clause). Third-party meshes
are downloaded during setup and remain gitignored.

## Architecture and requirements

The Go Tunnel dials Fabric with the configured robot identity/payee and is the
only process that verifies or settles x402. After successful verification it
publishes the authorized action to Zenoh. The K1 bridge consumes that event,
runs MuJoCo, and publishes one correlated terminal result. Webots independently
executes the same policy contract for Sim-to-Sim validation.
The K1 control process is part of this native Python bridge; ROS2 is not used
or required by this profile.

Required software:

- Git and Python 3.10 or newer;
- the repository's Go/zenoh-c Tunnel build dependencies;
- Webots R2025a for Webots and Sim-to-Sim checks;
- an OpenGL desktop for visual MuJoCo/Webots recording;
- Base Sepolia USDC only for the live settlement proof.

Default Zenoh topics:

| Purpose | Topic |
| --- | --- |
| authorized action | `robot/tunnel/action` |
| correlated result | `robot/tunnel/result` |
| K1 simulator metrics | `robot/booster_k1/metrics` |

All topics are configurable with `ZENOH_ACTION_TOPIC`, `ZENOH_RESULT_TOPIC`,
and `ZENOH_METRICS_TOPIC`.

## Clean checkout

From the repository root:

```bash
python -m pip install -r bridge/booster/k1_inspection_bridge/requirements.txt
python bridge/booster/k1_inspection_bridge/download_k1_model.py
python bridge/booster/k1_inspection_bridge/build_webots_model.py
```

On a machine that already has the listed simulator requirements, this clean
setup and validation flow is designed to complete in under 30 minutes.

Run MuJoCo headlessly or visually:

```bash
PYTHONPATH=bridge/booster/k1_inspection_bridge \
python bridge/booster/k1_inspection_bridge/run_inspection.py

PYTHONPATH=bridge/booster/k1_inspection_bridge \
python bridge/booster/k1_inspection_bridge/run_inspection.py \
  --viewer --viewer-hold-seconds 5
```

Run Webots and paired Sim-to-Sim validation:

```bash
PYTHONPATH=bridge/booster/k1_inspection_bridge \
python bridge/booster/k1_inspection_bridge/run_webots_validation.py

PYTHONPATH=bridge/booster/k1_inspection_bridge \
python bridge/booster/k1_inspection_bridge/run_sim2sim_validation.py
```

Set `WEBOTS_EXE` when Webots R2025a is not on `PATH`. Pass `--viewer` to the
Webots command for a recordable real-time window.

Expected headless success includes `status: "success"`, three confirmed
targets, and `policy_id: "booster-k1-active-inspection-v1-shared"`. The paired
command additionally requires `sim_to_sim_score: 1.0` and
`shared_policy_match: true`.

An intentionally invalid parameter or an interrupted inspection produces a
correlated failure instead of success, for example:

```json
{
  "action_id": "k1-demo-001",
  "robot_id": "booster-k1-sim-01",
  "skill_id": "inspect_target_sequence",
  "status": "failure",
  "result": {"success": false, "error_code": "INVALID_TARGETS"}
}
```

Failure and timeout results are terminal and make zero settlement calls.

## Paid execution

Deployment configuration is environment-only:

- `ROBOT_ID` (default `booster-k1-sim-01`)
- `ZENOH_ENDPOINT` or `ZENOH_CONFIG`
- `ZENOH_ACTION_TOPIC`, `ZENOH_RESULT_TOPIC`, `ZENOH_METRICS_TOPIC`
- `BOOSTER_K1_MUJOCO_VIEWER=1` and optional
  `BOOSTER_K1_MUJOCO_VIEWER_HOLD_SECONDS=5` for a paid visual recording
- `BOOSTER_K1_TARGET_HOLD_SECONDS=2` pauses only the live viewer after each
  measured target confirmation so left, center, and right remain independently
  visible; it does not change the closed-loop controller or simulator time
- `ROBO_PAYEE_ADDRESS` for the Tunnel deployment
- payer `PRIVATE_KEY` only for the explicit live Base Sepolia proof

Never use a mainnet wallet key for development. Keep the funded testnet payer
key and payee address in environment variables or repository secrets; never
place either key in config, command history, logs, examples, or artifacts.

The current shared Fabric Tunnel/proxy protocol identifies the robot using the
configured ID and payee but does not yet provide a signed robot-to-payee
handshake. This is an upstream protocol limitation, also documented by the
approved Tunnel implementation; this profile does not invent a second local
signature or claim stronger identity proof than the relay provides.

Start a Zenoh 1.9 router:

```bash
zenohd
```

Create an untracked Tunnel config from `tunnel/config.example.json`:

```json
{
  "robot_id": "booster-k1-sim-01",
  "evm_payee_address": "0xYOUR_BASE_SEPOLIA_PAYEE",
  "price": "0.001",
  "network": "eip155:84532"
}
```

Then start the K1 bridge and real Tunnel in separate terminals:

```bash
PYTHONPATH=bridge/booster/k1_inspection_bridge \
python -m k1_inspection_bridge.bridge

PROXY_WS_URL=wss://api.fabric.foundation/api/core/ws/robot \
FACILITATOR_URL=https://x402.org/facilitator \
ZENOH_ENDPOINT=tcp/127.0.0.1:7447 \
SKILL_CATALOG_PATH=registry/vendors/booster/k1/booster.k1.mujoco-webots-active-inspection.v1/skill-catalog.json \
ALLOWED_ACTIONS=inspect_target_sequence,stop \
bin/tunnel --config /path/to/untracked-k1-tunnel.json
```

Use `ZENOH_CONFIG` instead of `ZENOH_ENDPOINT` when the deployment needs an
explicit Zenoh JSON5 configuration. `PROXY_WS_URL` makes the Fabric relay
outbound connection configurable; `ROBOT_ID` may override the bridge default.

## Zenoh schemas and correlation

The Tunnel-published action has this shape (payment evidence is represented by
the verified `transaction_details`; it is not verified again robot-side):

```json
{
  "payload": {
    "skillId": "inspect_target_sequence",
    "params": {"maxDurationSec": 18, "targets": ["left", "center", "right"], "speedScale": 1.0}
  },
  "action_id": "k1-demo-001",
  "robot_id": "booster-k1-sim-01",
  "skill_id": "inspect_target_sequence",
  "idempotency_key": "k1-demo-001",
  "params_hash": "sha256:<hex>",
  "params_canonical": "{...}",
  "transaction_details": {"payment_requirements": {"network": "eip155:84532"}}
}
```

The bridge rejects incomplete, foreign, action/skill-mismatched, or
hash-inconsistent events before simulation. Its result preserves the exact
correlation tuple:

```json
{
  "action_id": "k1-demo-001",
  "robot_id": "booster-k1-sim-01",
  "skill_id": "inspect_target_sequence",
  "idempotency_key": "k1-demo-001",
  "params_hash": "sha256:<hex>",
  "status": "success",
  "profile_id": "booster.k1.mujoco-webots-active-inspection.v1",
  "result": {"targets_confirmed": ["left", "center", "right"]}
}
```

The Tunnel must verify x402 before publishing `robot/tunnel/action`. The bridge
preserves `action_id`, `robot_id`, `skill_id`, `idempotency_key`, and
`params_hash` in its result. Settlement is allowed only for a correlated
`status: success`. An invalid signature, nil/false verification, execution
failure, timeout, replay, or interrupted inspection cannot settle.

For the live test, configure a funded **testnet-only** payer key and payee in
the environment, then run:

```bash
python bridge/booster/k1_inspection_bridge/test_base_sepolia_tunnel_e2e.py
```

The script performs skill discovery, verifies the initial HTTP 402, executes
the first paid action after a clean start without a warm-up action, waits for
an explicit bridge-subscriber readiness marker before starting the Tunnel,
waits for the correlated result, and records the Base Sepolia transaction hash
without writing the private key.

On Windows, `run_live_base_sepolia_visual.ps1` starts an isolated local Zenoh
router, runs the Linux Tunnel in WSL, keeps the native MuJoCo viewer on Windows,
prints the exact source commit, and opens the matching BaseScan transaction only
after correlated success and settlement. Each confirmed target can be held in
the live viewer for a bounded wall-clock interval without advancing physics.

An unpaid manual request is useful for confirming discovery and the payment
challenge before spending testnet USDC:

```bash
curl -i -X POST \
  "https://api.fabric.foundation/api/core/robots/booster-k1-sim-01/action" \
  -H "Content-Type: application/json" \
  --data @registry/vendors/booster/k1/booster.k1.mujoco-webots-active-inspection.v1/examples/action-envelope.inspect_target_sequence.json
```

Expected result is HTTP 402 with `PAYMENT-REQUIRED`. Use the live Python test
for the paid retry; it builds the `PAYMENT-SIGNATURE`, expects HTTP 202, polls
the status endpoint, and requires `state: succeeded`, `settled: true`, and a
Base Sepolia transaction hash.

## Mandatory checks

```bash
PYTHONPATH=bridge/booster/k1_inspection_bridge \
python -m unittest \
  bridge/booster/k1_inspection_bridge/tests/test_policy.py \
  bridge/booster/k1_inspection_bridge/tests/test_bridge_contract.py

python bridge/booster/k1_inspection_bridge/tests/test_payment_gate.py
python bridge/booster/k1_inspection_bridge/tests/test_x402_no_settlement.py
python scripts/registry/validate_profiles.py --registry-root registry/vendors/booster
```

`test_payment_gate.py` sends paid-shaped evidence that the facilitator rejects
with `isValid:false` and with a missing verification verdict; required outcome
is HTTP 402, zero ActionEvents, zero simulator commands, and zero settlement
calls. `test_x402_no_settlement.py`
requires failure, timeout, idempotency replay, and payment replay to remain
unsettled.

## Safe stop

`stop` sets an interruption flag. MuJoCo applies the neutral 22-motor PD
command and zeros articulated joint velocity. The stop action succeeds only
after this is confirmed; the interrupted inspection reports
`completion_reason: safe_stopped` and fails, so it cannot settle.

## Troubleshooting

- `Official Booster K1 assets were not found`: run `download_k1_model.py`; a
  hash mismatch means the download does not match the pinned commit.
- `Webots R2025a not found`: set `WEBOTS_EXE` to the `webots` executable and
  rerun `build_webots_model.py`.
- Tunnel never appears in discovery: verify `PROXY_WS_URL`, `ROBOT_ID`, payee,
  and Tunnel logs for `ws connected to proxy`.
- No Zenoh action/result: start `zenohd` and ensure Tunnel and bridge use the
  same `ZENOH_ENDPOINT`/`ZENOH_CONFIG` and topics.
- HTTP 402 after a paid retry: inspect facilitator verification output; do not
  bypass it. Invalid or expired evidence must remain fail-closed.
- HTTP 409: the idempotency key or payment authorization was replayed; create a
  fresh paid request instead of resubmitting an executed action.
- Terminal `failed`/`timeout`: inspect the correlated simulator result. These
  states intentionally produce no settlement.
