# Unitree G1 Tier 1 bridge

This simulator-only bridge executes the paid `inspect_target_sequence` skill
with Unitree's official 29-DoF G1 model in MuJoCo and Webots R2025a. A disclosed
pelvis safety fixture keeps both feet at floor level; this profile makes no
walking, balance, physical-robot, or ROS 2 hardware claim.

## Official model provenance

`download_g1_model.py` pins and hashes two repositories owned by Unitree:

- MuJoCo: `unitreerobotics/unitree_mujoco` commit
  `ae6a8403e272733e9996ef59990880330496177f`, file
  `unitree_robots/g1/g1_29dof.xml`, canonical SHA-256
  `423e28bd718b19f7a65cda539b6f794ddbb268b4b9bdbd85f4bd982b30729617`.
- Webots source: `unitreerobotics/unitree_ros` commit
  `daadf41ee9afce8f90fdc09a98506012691fa122`, file
  `robots/g1_description/g1_29dof.urdf`, canonical SHA-256
  `aaa1e33640109dadaaae8bd89707f31f3d2f4efed6f148283e30ff7e1ee22131`.

Both upstream BSD-3-Clause licenses are copied into the ignored model cache.
The full Webots viewer references every official STL vertex. CI uses separate
visual-only derivatives capped at 6,000 faces per link; all 29 actuated joints,
kinematics, inertias, limits, controller inputs, and policy state are retained.

## Task model

The shared `unitree-g1-29dof-active-inspection-v1-shared` controller commands
bounded waist and arm goals for the left, center, and right targets. It reads
measured joint state every tick and confirms a target only after maximum error
stays within 0.075 rad for 0.55 seconds. This is a feedback-gated task model,
not playback or a prerecorded animation.

MuJoCo applies PD torque plus model-derived gravity/Coriolis compensation.
Webots applies the same goals through position motors. Sim-to-Sim succeeds only
when both engines confirm all three targets and expose the same policy ID,
target order, limits, dwell, speed scale, and fixture declaration.

`stop` commands the neutral 29-motor pose, zeros MuJoCo joint velocity, and
causes any interrupted inspection to return failure without settlement.

## Clean setup and simulation

```bash
python -m pip install -r bridge/unitree/g1_inspection_bridge/requirements.txt
python bridge/unitree/g1_inspection_bridge/download_g1_model.py
```

MuJoCo:

```bash
PYTHONPATH=bridge/unitree/g1_inspection_bridge \
python bridge/unitree/g1_inspection_bridge/run_inspection.py \
  --json-output bridge/unitree/g1_inspection_bridge/artifacts/mujoco_result.json

PYTHONPATH=bridge/unitree/g1_inspection_bridge \
python bridge/unitree/g1_inspection_bridge/run_inspection.py \
  --viewer --viewer-target-hold-seconds 2 --viewer-hold-seconds 3
```

Native Windows Webots and paired validation:

```powershell
$env:PYTHONPATH = (Resolve-Path 'bridge/unitree/g1_inspection_bridge').Path
python bridge/unitree/g1_inspection_bridge/build_webots_model.py
python bridge/unitree/g1_inspection_bridge/run_webots_validation.py --viewer
python bridge/unitree/g1_inspection_bridge/run_sim2sim_validation.py --timeout 300
```

## Paid action path and safety

```text
paid HTTP action -> Fabric Gateway WebSocket -> real Go Tunnel
  -> synchronous x402 verification -> Zenoh robot/tunnel/action
  -> G1 bridge -> live simulator episode -> correlated robot/tunnel/result
  -> durable Tunnel status -> deferred settlement only after exact success
```

The Tunnel rejects missing, nil, or `isValid: false` verification before
`PostAction`. Mandatory regression tests require HTTP 402, zero ActionEvents,
zero simulator state change, and zero settlement calls. The Tunnel durably
reserves both idempotency keys and payment fingerprints before publication, so
replays remain HTTP 409 after restart.

Topics:

| Purpose | Zenoh key |
| --- | --- |
| verified actions | `robot/tunnel/action` |
| correlated results | `robot/tunnel/result` |
| G1 simulator metrics | `robot/unitree_g1/metrics` |

Every result preserves `action_id`, `robot_id`, `skill_id`, `idempotency_key`,
and `params_hash`. Failure, timeout, replay, stop interruption, mismatched
correlation, or settlement failure can never be reported as paid success.

For a manual bridge session:

```bash
zenohd -l tcp/0.0.0.0:7447
export ZENOH_ENDPOINT=tcp/127.0.0.1:7447
export ROBOT_ID=unitree-g1-sim-01
export PYTHONPATH=bridge/unitree/g1_inspection_bridge
python -m g1_inspection_bridge.bridge
```

The subscriber is declared before `UNITREE_G1_READY_FILE` is written, allowing
the first paid action after a clean start to execute without a warm-up action.

## Tests

```bash
PYTHONPATH=bridge/unitree/g1_inspection_bridge python -m unittest \
  bridge/unitree/g1_inspection_bridge/tests/test_policy.py \
  bridge/unitree/g1_inspection_bridge/tests/test_bridge_contract.py \
  bridge/unitree/g1_inspection_bridge/tests/test_webots_model.py

python bridge/unitree/g1_inspection_bridge/tests/test_payment_gate.py
python bridge/unitree/g1_inspection_bridge/tests/test_x402_no_settlement.py
python scripts/registry/validate_profiles.py --registry-root registry/vendors/unitree
```

The payment scripts exercise the real Linux Tunnel and Zenoh transport. They
cover invalid verification, missing verdict, execution failure, timeout,
durable idempotency replay, payment replay, paid stop, correlation, and
execution-gated settlement.

## Base Sepolia current-head recording

Trusted push and manual workflow runs receive repository secrets; untrusted
pull-request events do not. The visual runner loads `PRIVATE_KEY` (or
`BASE_SEPOLIA_PRIVATE_KEY`) and `ROBO_PAYEE_ADDRESS` only from the process
environment. Secrets are never committed, printed, or passed on the command
line.

```powershell
./bridge/unitree/g1_inspection_bridge/run_live_base_sepolia_visual.ps1 `
  -TargetHoldSeconds 2 -FinalHoldSeconds 3 -ViewerStartSeconds 8 -PauseAfter
```

The runner prints the exact commit and pauses for Enter. Keep its terminal and
the complete MuJoCo viewer simultaneously visible through unpaid HTTP 402,
first paid HTTP 202, all three target confirmations, correlated success,
settlement, and the matching BaseScan page. Bind the commit, action ID,
transaction hash, recording SHA-256, and reviewed JSON artifact in the evidence
manifest only after a successful run.

## Troubleshooting

- Rerun `download_g1_model.py` after a missing asset or hash failure. Never
  substitute another humanoid mesh.
- Set `WEBOTS_EXE` if native Webots R2025a is not discovered automatically.
- Build the Tunnel with `make build` in Ubuntu/WSL before a Windows recording.
- If a paid request remains HTTP 402, verify the challenge network, funded
  payer, configured payee, facilitator, and authorization window without
  printing the key.
- A `settlement_failed` artifact is failure evidence, never a successful live
  receipt. Use a fresh action and payment for the next attempt.
