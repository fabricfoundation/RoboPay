# Webots sim-to-sim runtime

Real-measurement harness for the MuJoCo Go2 model against an independent
physics engine (Webots R2025a, ODE).

## What this is

`test_sim2sim_go2_webots.py` re-runs every paid Go2 skill in MuJoCo, captures
the joint configuration at each salient moment, and — when the Webots runtime
is present — applies the **same joint targets** to the Go2 model in Webots
through the Supervisor API, then reads the foot-tip positions reported by the
Webots physics engine and computes a real measured error against the MuJoCo
baseline.

## Honesty contract

- Without the Webots runtime the harness writes
  `go2_webots_sim2sim_report.json` with
  `"verdict": "skipped_webots_runtime_missing"` and exits 0. It does **not**
  claim a measured result, and `max_error_m` is `null`.
- No placeholder values are ever written. `max_error_m` is set only from real
  measurements. Nothing in this repository describes the Webots run as
  validated until a real `"verdict": "pass"` report exists.
- The Webots job in CI is best-effort (`continue-on-error: true`): a missing
  runtime downgrades to SKIP, never to a false pass.

## Model

`go2_sim2sim.wbt` is rebuilt from the MuJoCo Menagerie Go2 model
(`unitree_go2/go2.xml`, commit
`da76818e269b82289eba39808e2fb91d679d6994`): same joint anchors/axes/ranges,
motor torque limits, body masses / centers of mass / diagonal inertias, and
foot-tip placement. Device names match what the MuJoCo controller writes
(`FL_hip_joint` … `RR_calf_joint`) and foot nodes are `DEF FL_foot` /
`FR_foot` / `RL_foot` / `RR_foot`. The world is authored by hand from the MJCF
(no converter dependency) so every value is a direct copy from `go2.xml`.

## World conventions

A Webots world that plugs into this harness must provide:

| Convention            | Value                                              |
| --------------------- | -------------------------------------------------- |
| Robot node            | `DEF GO2`, `supervisor TRUE`, controller `go2_sim2sim` |
| Motor (Servo) names   | `FL_hip_joint` … `RR_calf_joint` (12 total)        |
| Foot node DEFs        | `FL_foot`, `FR_foot`, `RL_foot`, `RR_foot`         |
| Foot contact material | `go2` (vs `ground`) for the floor                  |

## Run

```bash
cd simulation/webots
bash run_webots_sim2sim.sh
```

The script launches Webots headless (`xvfb-run … --mode=fast`) and exits
non-zero only on a real `fail`. When the Webots binary is unavailable it runs
the harness in SKIP mode (exit 0, no measured result).
