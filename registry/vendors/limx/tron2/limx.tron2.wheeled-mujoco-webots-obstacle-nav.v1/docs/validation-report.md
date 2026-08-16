# LimX TRON 2 validation report

## Scope

Tier 1 simulator-only integration of the official `WF_TRON2A` model for the
fixed `navigate_obstacle_course` and `stop` skills. Payment price is `$0.001`
USDC on Base Sepolia.

- Revalidated: 2026-08-16
- Simulator host: Windows, MuJoCo 3.3.0, Webots R2025a, Python 3.12
- Tunnel contract host: Ubuntu 22.04 under WSL2, Go 1.25, Zenoh/zenoh-c 1.9.0,
  Python 3.10
- ROS2: not used; this profile connects the Tunnel and simulator directly over
  the documented Zenoh topics

Validated skills: `navigate_obstacle_course`, `stop`.

## Official asset provenance

- Robot description: `limxdynamics/tron2-robot-description`, commit
  `682d513d03f7e3d2a59ae791d50adc5ccb84dd1a`, upstream path
  `tron2/WF_TRON2A`.
- Controller: `limxdynamics/tron2_rl_deploy_python`, commit
  `16db4e19eb28664a101fed7135d20d6c7f52bd38`, upstream path
  `controllers/model/WF_TRON2A`.
- All 16 required URDF/MJCF/STL/ONNX/YAML assets were compared byte-for-byte
  with those commits and match the SHA-256 values in `robot.profile.yaml`.
- Binary assets are downloaded at setup and are not redistributed in this
  contribution. The upstream legal notices and model card are retained.

## Locally revalidated results

- MuJoCo 3.3.0: 10/10 waypoints, three obstacles detected, goal reached, no
  collision, `0.1976 m` minimum clearance, `5.3732 m` measured final x and
  `0.2398 m` final goal distance.
  The official LimX policy/encoder ONNX drives the pinned vendor MJCF at the
  actuator level.
- Webots R2025a: 10/10 waypoints, three obstacles detected, goal reached, no
  obstacle contact, `0.2345 m` minimum clearance, `5.3638 m` measured base
  displacement and `0.2397 m` final goal distance. Task-level validation runs
  the same online route planner and maps its commands to bounded Supervisor
  chassis velocity (maximum `0.25 m/s`) at a 500 Hz physics rate. Measured
  pose, velocity, orientation, contacts and obstacle clearance are terminal
  authority; the controller performs zero root translation/rotation writes.
- Sim-to-Sim contract: same model variant, course, waypoint count, obstacles,
  success boundary and measured terminal goal state.
- Real Zenoh: one valid correlated event executes once; replay publishes a
  terminal rejection and does not execute a second time.
- Durable restart: repeated action ID/idempotency key and repeated payment
  fingerprint are rejected after opening a fresh store instance.
- Real Go Tunnel/x402 middleware with a recording facilitator: a paid-shaped
  tampered signature receiving `isValid:false` returns HTTP 402, publishes zero
  ActionEvents, produces zero simulator state changes and makes zero settlement
  calls. Injected simulator failure and timeout remain unsettled; replay causes
  no second dispatch.

## Commands

```powershell
$env:PYTHONPATH = "$PWD/bridge"
py -3 -m pytest -q tests
py -3 bridge/download_vendor_assets.py --verify-only
py -3 bridge/run_mujoco_obstacle_course.py
$env:WEBOTS_EXE = 'C:\path\to\Webots\webots.exe'
py -3 bridge/run_sim2sim_validation.py
```

The x402 integration test requires the real Linux Tunnel built by `make build`
and runs in WSL/Linux with `TUNNEL_BIN=.../bin/tunnel` and
`LD_LIBRARY_PATH=.../.zenoh-c/lib`.

Local revalidation completed with `15 passed` for the full profile suite and
all Go Tunnel packages passing. The Sim-to-Sim report returned score `1.0`.

## CI acceptance matrix

- `tunnel-and-contract` is mandatory on the pull request: Go build/tests,
  registry validation, contract tests and the explicit `isValid:false` gate.
- `tron2-mujoco` is mandatory: official ONNX actuator-level execution, real
  Zenoh correlation and durable replay protection.
- `tron2-sim2sim` is mandatory: independent MuJoCo and Webots execution of the
  same task contract with uploaded measured-state evidence.
- `tron2-base-sepolia-e2e` is mandatory evidence on the trusted fork branch or
  a manual trusted dispatch, but intentionally skipped on an external PR where
  GitHub withholds secrets.

The workflow passes `actionlint` 1.7.7. Python dependencies are exact-pinned;
in particular `requests==2.33.0`, avoiding the older Reachy Mini pin conflict.

## Current visual and live-chain evidence

The continuous 59.47-second operator recording is bound to source commit
`d11ab49fdb051c8eb9fa73fb216b2b46f2c638ab`, action ID
`limx-tron2-navigation-1786907880`, and trusted artifact
`artifacts/base_sepolia_result_1786907925.json`. It shows the terminal and
MuJoCo viewer together from the unpaid `HTTP 402` and first paid `HTTP 202`
through all 10 waypoints, all three obstacles, the final goal hold, correlated
`succeeded` result, execution-gated `settled: true`, and the matching BaseScan
page.

- Base Sepolia transaction:
  `0xa29da1b44475b36b62f70ed63a60c8dfb56263d53e31cbbcdab94ce877863e39`
- Recording SHA-256:
  `4b7bce1d7e963d3cb71183cfd894dfbaf051ef452e23f24e6d79a89f32dec47b`
- Recording:
  https://github.com/user-attachments/assets/16a4a1bc-0fdb-4387-b47f-11701149ae4d

## Deliberate boundaries

MuJoCo provides actuator-level validation with the pinned LimX reinforcement-
learning controller. Webots provides task-level Sim-to-Sim validation with the
same online route planner and a bounded chassis-velocity adapter on the dynamic
official model. Webots uses Supervisor to read measured simulator state and
contacts and to issue velocity commands; it never writes or resets root
translation/rotation and never replays a recorded trajectory. Identity
signing between the shared Gateway and robot WebSocket remains an upstream
protocol boundary.

`stop` is deliberately scoped as an idle safe-stop: it confirms that no
navigation episode starts and that zero velocity is retained. It is not an
asynchronous cancellation mechanism for an already-running episode.

The live Base Sepolia job requires fork secrets and therefore runs only on a
trusted push to `limx-tron2-tier-1` or through `workflow_dispatch`; it is
expected to be skipped on an external upstream pull request. The current
operator recording and its matching trusted JSON artifact are captured and
linked above and in `docs/evidence/evidence-manifest.yaml`.
