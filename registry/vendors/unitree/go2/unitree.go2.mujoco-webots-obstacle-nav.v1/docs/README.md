# Unitree Go2 simulator profile

Profile: `unitree.go2.mujoco-webots-obstacle-nav.v1`
Tier: simulator-only Tier 1
Skills: `navigate_obstacles`, `stop`
Price: `0.001` USDC on Base Sepolia (`eip155:84532`)

The paid skill executes a bounded online controller in MuJoCo and is validated
against Webots R2025a. It is not a built-in demo or replay. Both engines use
the same policy ID, route, waypoint state machine, foot-space gait parameters,
and terminal criteria. MuJoCo uses the official Unitree MJCF torque motors;
Webots uses the official Unitree URDF converted into actual joint motors.

Terminal success requires all of the following measured simulator state:

- goal distance no greater than 0.32 m;
- all corridor waypoints completed;
- zero physical contacts with either obstacle;
- stable, finite body state throughout execution.

The shared Tunnel verifies payment before publishing, returns an immediate
`202` carrying `action_id`, persists payment-bound replay reservations, and
settles only a correlated terminal success. A facilitator `isValid:false`
response returns `402`, with zero ActionEvents and zero settlement calls.
Injected simulator failure/timeout and replay are mandatory CI tests and remain
unsettled.

Model provenance and exact commits are in
`bridge/unitree/go2_mujoco_bridge/models/model.lock.json`. Reproduction and
graphical commands are in `bridge/unitree/go2_mujoco_bridge/README.md`.

## Current-head visual evidence

The visual launcher prints the exact Git commit before discovery and writes the
same value as `source_commit` into its trusted JSON result. A recording must
keep the terminal and native MuJoCo viewer visible together from the unpaid
`402` through every route point, the short final-state hold, the correlated
result, and BaseScan. The required action, transaction, recording, and JSON
integrity bindings are tracked in `docs/evidence/evidence-manifest.yaml`; it
intentionally remains pending until the MP4 and SHA-256 exist.
