# Unitree Go2 — Tier 1: RoboPay-triggered navigation in simulation

Connects RoboPay with a simulated Unitree Go2 and triggers the action by a
policy: a paid action arriving on the tunnel's Zenoh topic starts a
goal-conditioned obstacle-navigation episode, executed by an A* planner and
a trot gait controller on the official Go2 model — in **two** simulators
(MuJoCo and Webots) with a quantitative sim-to-sim comparison.

![Go2 navigating the obstacle course in MuJoCo](docs/go2_nav.gif)

```
paid action (x402 / AIP)                    [simulated payment settlement]
        │
        ▼
tunnel (Go, this repo) ──► Zenoh topic robot/tunnel/action
        │                                          │
        │ (same event schema                       ▼
        │  as handlers.PostAction)      robopay_link.py subscriber
        │                                          │
        ▼                                          ▼
   A* planner over the obstacle map ──► pure-pursuit ──► velocity command
                                                           │
                                                           ▼
                       trot gait generator (Raibert placement) ──► leg IK
                                                           │
                                                           ▼
                                    joint PD ──► 12 motor torques
                                                           │
                                          ┌────────────────┴───────────┐
                                          ▼                            ▼
                                   MuJoCo (official           Webots (official
                                   menagerie Go2)             Unitree URDF → PROTO)
```

Nothing is replayed: obstacle layout and goal are parameters, the plan is
computed from them, and every torque comes from feedback on the live
physics state. Changing the goal or the layout changes the trajectory
(verified by test).

## Requirements

- Python 3.10+ with `pip install mujoco eclipse-zenoh numpy`
- Go 1.21+ (tunnel; `make build` fetches zenoh-c automatically)
- [Webots R2025a](https://cyberbotics.com) (for the sim-to-sim validation;
  expected at `/Applications/Webots.app` on macOS — adjust the constants in
  `webots/run_webots.py` for other platforms)

Validated on macOS arm64 (Apple Silicon).

## Setup

```sh
cd simulation
./setup.sh          # fetches the official Go2 model assets (pinned commits)
cd .. && make build # builds the tunnel binary (bin/tunnel)
```

## Run

**End-to-end paid action → robot moves (A1):**

```sh
cd simulation/go2
python3 test_link.py
```

Starts the real tunnel binary, proves its Zenoh session is live (config
round-trip appears in the tunnel log), publishes a simulated paid action
with the exact `handlers.PostAction` event schema to `robot/tunnel/action`,
and asserts the MuJoCo episode reaches the goal. To watch the pieces
individually: `python3 robopay_link.py` in one terminal,
`python3 simulate_paid_action.py 9.0 1.5` in another.

Only the payment settlement is simulated — topic and schema match the
tunnel exactly, and both payment rails (x402 `POST /action` and the AIP
agent) publish to this same topic, so the simulation-side integration is
identical for either. The repo's ROS 2 bridge targets Isaac Sim on Linux;
subscribing to the same Zenoh topic is the equivalent integration point for
a Python simulation on macOS.

**Test suite (MuJoCo side):**

```sh
cd simulation/go2
python3 test_ik.py    # leg IK vs MuJoCo forward kinematics (machine precision)
python3 test_gait.py  # stand / walk / turn / arc acceptance with metrics JSON
python3 test_nav.py   # 5 navigation episodes incl. competitor-aligned layout
```

**Sim-to-sim validation (A5):**

```sh
cd simulation/webots
python3 test_sim2sim.py
```

Runs the same tasks in MuJoCo and Webots with the *identical* policy stack
(`go2_gait.py` + `go2_nav.py` imported unchanged by the Webots controller;
only simulator I/O differs) and writes `sim2sim_report.json`.

## Sim-to-sim results

| layout | simulator | reached | collisions | time | path length |
|---|---|---|---|---|---|
| A (3 obstacles, goal 10 m ahead) | MuJoCo | yes | 0 | 25.96 s | 10.64 m |
| A | Webots | yes | 0 | 24.78 s | 10.16 m |
| B (different layout + goal) | MuJoCo | yes | 0 | 21.96 s | 8.83 m |
| B | Webots | yes | 0 | 20.74 s | 8.45 m |

Trajectory agreement between the simulators: **mean gap 2.4–2.5 cm**, max
gap ≤ 6.1 cm, duration ratio 0.94–0.96 (`sim2sim_report.json`).

## Metrics (from the physics engine)

Every episode reports JSON with: final goal distance, path completion,
path length, collision count (foot–ground touchdowns excluded), elapsed
sim time, and the trajectory. In MuJoCo, collisions are counted from
`mjData.contact` pairs; in Webots, from supervisor contact points on the
obstacle solids (verified by a teleport-into-obstacle probe).

## Layout

```
simulation/
├── setup.sh                 # fetch pinned official Go2 model assets
├── go2/                     # gait + navigation + RoboPay link (MuJoCo)
│   ├── go2_gait.py          #   trot gait: Raibert placement, leg IK, joint PD
│   ├── go2_nav.py           #   A* planner + pure pursuit + episode metrics
│   ├── robopay_link.py      #   Zenoh subscriber: paid action → episode
│   ├── simulate_paid_action.py
│   └── test_*.py            #   ik / gait / nav / link acceptance tests
└── webots/
    ├── protos/Go2.proto     # from official unitree_ros URDF via urdf2webots
    ├── worlds/go2_nav.wbt   # generated by make_world.py (layout A)
    ├── controllers/go2_nav_webots.py  # same policy stack, Webots I/O
    ├── make_world.py        # layout-parameterized world generation
    ├── run_webots.py        # headless episode runner (extern controller)
    ├── test_sim2sim.py      # MuJoCo vs Webots comparison
    └── sim2sim_report.json
```
