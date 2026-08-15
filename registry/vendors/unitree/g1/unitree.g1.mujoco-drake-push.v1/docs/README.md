# Unitree G1 — paid manipulation in simulation

**Simulator-only submission.** No physical robot is involved.

A payer names where an object is and where it should end up. The robot turns to
face it, plans a collision-free path over it with a constrained IK solver, and
pushes it to the commanded destination. Payment is verified before anything
moves, and a failed action never settles.

```
payer ──x402──► tunnel ──Zenoh robot/tunnel/action──► bridge ──► MuJoCo
                                                        │
                    Zenoh robot/tunnel/result ◄─────────┘  settle: true|false
```

## What makes this not a replayed animation

Both ends of the motion are parameters of the paid action. The turn angle comes
from the puck's *observed* bearing, and every waypoint is solved at run time by
a constrained IK solver against its *measured* pose. There is no trajectory to
replay, because the trajectory does not exist until the request arrives.

Stage transitions fire on sensed conditions — joint convergence, achieved puck
displacement. Timers exist only as failure guards.

## Setup

Needs Python 3.13 (Drake ships macOS wheels only for 3.13+) and about 10
minutes, most of it downloads.

```bash
# 1. Python dependencies
python3.13 -m venv .venv
.venv/bin/pip install mujoco==3.10.0 eclipse-zenoh==1.9.0 \
    "x402[requests,evm]==2.16.0" eth-account==0.13.7 requests drake numpy trimesh

# 2. Robot descriptions
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/google-deepmind/mujoco_menagerie.git ~/menagerie
git -C ~/menagerie sparse-checkout set unitree_g1

git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/unitreerobotics/unitree_ros.git ~/g1_urdf
git -C ~/g1_urdf sparse-checkout set robots/g1_description

# 3. Convert meshes for Drake
#    Drake computes convex hulls for collision geometry and accepts .obj,
#    .vtk or .gltf; the Unitree description ships .STL exclusively.
.venv/bin/python bridge/unitree/g1/sim_bridge/tools/convert_meshes.py \
    --src ~/g1_urdf/robots/g1_description \
    --dest ./assets/g1_description_obj
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ROBOT_ID` | `g1-sim-001` | Identity every envelope must be addressed to |
| `ZENOH_LISTEN` | `tcp/127.0.0.1:7447` | Endpoint the bridge accepts connections on |
| `ZENOH_ENDPOINT` | `tcp/127.0.0.1:7447` | Endpoint the client dials |
| `ZENOH_ACTION_TOPIC` | `robot/tunnel/action` | Incoming paid actions |
| `ZENOH_RESULT_TOPIC` | `robot/tunnel/result` | Terminal results |
| `ZENOH_CONFIG` | unset | Full Zenoh JSON5 config; overrides the above |
| `G1_MENAGERIE_DIR` | `~/menagerie/unitree_g1` | MuJoCo model location |
| `G1_URDF` | `./assets/g1_description_obj/...` | Drake model location |

**Private keys are never read by this bridge and never belong in this
repository.** Payment verification is the tunnel's job; the bridge only sees
whether the tunnel marked a payment verified and what settlement reference it
attached. Keep signing keys in the environment or a secret manager, and note
that the bridge does not log payment payloads.

### Zenoh

No router or extra install is needed. The bridge listens on an explicit TCP
endpoint and the client dials it. Multicast scouting is deliberately not relied
on — it fails silently in some environments and looks like a hung bridge.

## Run the demo

Terminal 1:

```bash
cd bridge/unitree/g1
python -m sim_bridge.main --robot-id g1-sim-001
```

Terminal 2 — the happy path:

```bash
cd bridge/unitree/g1
python -m sim_bridge.tools.send_action --puck 0.34 -0.20 --goal 0.44 -0.04
```

Expected:

```
--- attempt 1/1: action act_… skill=push_to_target key=idem-…
    status=success settle=True
    displacement=0.146m final_distance=0.05m contacts=4 sim=13.5s
```

### The failure paths

Each of these must refuse the action and must **not** settle:

```bash
python -m sim_bridge.tools.send_action --unpaid                 # PAYMENT_REQUIRED
python -m sim_bridge.tools.send_action --tamper                 # PARAMS_HASH_MISMATCH
python -m sim_bridge.tools.send_action --expired                # ACTION_EXPIRED
python -m sim_bridge.tools.send_action --puck 0.36 -0.90        # PARAMS_OUT_OF_RANGE
python -m sim_bridge.tools.send_action --skill diagnostic_fail  # ACTION_FAILED
python -m sim_bridge.tools.send_action --repeat 2               # second is IDEMPOTENCY_REPLAY
```

Expected for the unpaid case:

```
    status=error settle=False
    error=PAYMENT_REQUIRED: payment has not been verified by the tunnel
```

### Sim-to-Sim validation

```bash
python -m sim_bridge.simulation.sim2sim --puck 0.34 -0.20 --goal 0.44 -0.04
```

Runs the identical policy in MuJoCo and in Drake and compares the outcomes.
Exit code 0 when they agree.

### Everything at once

```bash
python -m sim_bridge.tools.collect_evidence
```

Reproduces every table in `validation-report.md`. Takes a few minutes.

## Tests

```bash
pytest bridge/unitree/g1/sim_bridge/tests
```

Covers envelope parsing, parameter validation, action routing, the success and
failure response shapes, and the settlement rule. These need no simulator.

## Troubleshooting

**`no result within timeout`** — the client cannot reach the bridge. Check the
bridge printed `zenoh endpoint tcp/127.0.0.1:7447`, and that `ZENOH_ENDPOINT`
matches. Nothing else listens on that port by default.

**`menagerie G1 model not found`** — set `G1_MENAGERIE_DIR`, or re-run the
sparse checkout in step 2.

**`G1 URDF not found`** — run `convert_meshes.py` (step 3). Drake cannot load
the STL meshes the Unitree description ships.

**`MakeConvexHull only applies to .obj, .vtk, and .gltf`** — same cause: Drake
is being pointed at the unconverted description.

**`PARAMS_OUT_OF_RANGE`** — the target is outside the arm's reachable set. The
published ranges are in `skills.yaml`; the boundary was measured, not guessed.

**A run takes 10–25 simulated seconds.** Expected. The correction that trims the
arm onto its waypoint is deliberately slow; see the validation report.

## Files

```
bridge/unitree/g1/sim_bridge/
  main.py                     Zenoh bridge entrypoint
  g1/action_contract.py       paid action envelope, and the rules for refusing one
  g1/mapper.py                skill catalogue, prices, parameter validation
  g1/node.py                  execution and the settlement decision
  policy/controller.py        the finite-state plan
  policy/ik.py                constrained IK planner (Drake)
  policy/stages.py            stage definitions
  simulation/base.py          the contract both engines implement
  simulation/mujoco_env.py    primary engine
  simulation/drake_env.py     validation engine
  simulation/metrics.py       simulator state metrics, and engine comparison
  simulation/runner.py        task execution
  simulation/sim2sim.py       sim-to-sim validation
  tools/                      mesh conversion, action client, evidence collection
```
