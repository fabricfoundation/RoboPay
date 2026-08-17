# AgiBot X2 — paid manipulation in simulation

**Simulator-only submission.** No physical robot is involved.

A payer names where a puck is and where it should end up. The robot plans a
path over it with a constrained IK solver and pushes it to the commanded
destination. Payment is verified before anything moves, and a failed action
never settles.

```
payer ──x402──► tunnel ──Zenoh robot/tunnel/action──► bridge ──► MuJoCo
                                                        │
                    Zenoh robot/tunnel/result ◄─────────┘  settle: true|false
```

## What makes this not a replayed animation

Both ends of the motion are parameters of the paid action. Every waypoint is
solved at run time by a constrained IK solver against the puck's *measured*
pose, and the push direction comes from the live puck-to-goal vector. There is
no trajectory to replay, because the trajectory does not exist until the
request arrives.

Stage transitions fire on sensed conditions — tool proximity, achieved puck
displacement. Timers exist only as failure guards.

## Setup

Needs Python 3.13 (Drake ships macOS wheels only for 3.13+) and about 10
minutes, most of it downloads.

```bash
# 1. Python dependencies
python3.13 -m venv .venv
.venv/bin/pip install mujoco==3.10.0 eclipse-zenoh==1.9.0 \
    "x402[requests,evm]==2.16.0" eth-account==0.13.7 requests drake numpy \
    trimesh imageio imageio-ffmpeg pytest
```

```bash
# 2. Robot description
git clone --depth 1 https://github.com/AgibotTech/agibot_x2_urdf.git ~/x2
```

```bash
# 3. Convert meshes for Drake
#    Drake computes convex hulls for collision geometry and accepts .obj,
#    .vtk or .gltf; the AgiBot description ships .STL exclusively.
.venv/bin/python bridge/agibot/x2/sim_bridge/tools/convert_meshes.py \
    --src ~/x2/X2_URDF-v1.3.0 \
    --dest ./assets/x2_description_obj
```

MuJoCo runs `x2_ultra.xml` directly from the checkout. Drake runs the converted
copy of `x2_ultra_simple_collision.urdf`.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ROBOT_ID` | `x2-sim-001` | Identity every envelope must be addressed to |
| `ZENOH_LISTEN` | `tcp/127.0.0.1:7447` | Endpoint the bridge accepts connections on |
| `ZENOH_ENDPOINT` | `tcp/127.0.0.1:7447` | Endpoint the client dials |
| `ZENOH_ACTION_TOPIC` | `robot/tunnel/action` | Incoming paid actions |
| `ZENOH_RESULT_TOPIC` | `robot/tunnel/result` | Terminal results |
| `ZENOH_METRICS_TOPIC` | `robot/x2/metrics` | Per-run simulator metrics |
| `ZENOH_CONFIG` | unset | Full Zenoh JSON5 config; overrides the above |
| `X2_DESCRIPTION_DIR` | `~/x2/X2_URDF-v1.3.0` | MuJoCo model location |
| `X2_DESCRIPTION_OBJ` | `./assets/x2_description_obj` | Drake model location |

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
cd bridge/agibot/x2
python -m sim_bridge.main --robot-id x2-sim-001
```

Terminal 2 — the happy path:

```bash
cd bridge/agibot/x2
python -m sim_bridge.tools.send_action --puck 0.26 0.17 --goal 0.27 0.30
```

The same client exercises every rejection path:

```bash
python -m sim_bridge.tools.send_action --unpaid            # PAYMENT_REQUIRED
python -m sim_bridge.tools.send_action --tamper            # PARAMS_HASH_MISMATCH
python -m sim_bridge.tools.send_action --expired           # ACTION_EXPIRED
python -m sim_bridge.tools.send_action --repeat 2          # IDEMPOTENCY_REPLAY
python -m sim_bridge.tools.send_action --skill diagnostic_fail   # ACTION_FAILED
```

Every one of those returns `settle=false`. `diagnostic_fail` exists so the
no-settle-on-failure guarantee can be demonstrated on demand rather than
argued for.

## Verify it without the bridge running

```bash
cd bridge/agibot/x2

pytest sim_bridge/tests                                    # 42 tests

# The same task in both engines, side by side
python -m sim_bridge.simulation.sim2sim --puck 0.26 0.17 --goal 0.27 0.30

# Every claim in the validation report, re-measured
python -m sim_bridge.tools.collect_evidence --sim2sim-cases 10 --json
```

## Skill

`push_to_target` — 0.01 USDC, Base Sepolia.

| param | range (m) |
|---|---|
| `puck_x` | 0.255 … 0.270 |
| `puck_y` | 0.160 … 0.200 |
| `goal_x` | 0.255 … 0.285 |
| `goal_y` | 0.290 … 0.320 |

Push distance must fall in `[0.10, 0.17] m`. Success is the puck ending within
50 mm of the goal, measured from simulator state.

`stop` is free. `diagnostic_fail` always fails.

The envelope is narrow, and deliberately so. It was measured by sweeping
targets through *both* engines and keeping only what both deliver — the
binding limit is not the arm's reach but the travel leg, which needs the wrist
near shoulder height because the hand hangs 165 mm below it. Requests outside
the box are refused with `PARAMS_OUT_OF_RANGE` rather than attempted, because
a skill that quotes a price should only quote it where it delivers.

## Results

10 / 10 targets delivered, 10 / 10 sim-to-sim verdicts matching, worst
inter-engine disagreement 51 mm against a 100 mm tolerance, and all 9 payment
gate rules behaving as specified. Full numbers, method, and the three model
defects that had to be fixed before the two engines could be compared honestly:
[validation-report.md](validation-report.md).

Recording of a paid action, with the correlated bridge log and result
envelopes, in [evidence/](evidence/).

## Layout

```
bridge/agibot/x2/sim_bridge/
  main.py                  Zenoh bridge entry point
  x2/action_contract.py    envelope parsing, canonical params hash
  x2/mapper.py             skill catalogue and the operating envelope
  x2/node.py               payment gate, idempotency, settlement rule
  policy/ik.py             constrained IK, tool point, joint selection
  policy/controller.py     the staged push policy
  simulation/mujoco_env.py primary engine
  simulation/drake_env.py  validation engine
  simulation/sim2sim.py    the side-by-side comparison
  tools/                   client, evidence collector, demo recorder
  tests/                   42 tests, no simulator required
```
