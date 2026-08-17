# DeepRobotics X30 Pro — inspection-lane simulator bridge

This robot-scoped bridge exposes one paid motion skill, `perform_inspection_gait`, and safe `stop` for the X30 profile. A paid inspection action has no caller-controlled motion parameters: it executes the profile's fixed, bounded inspection lane and reports a correlated terminal result from simulator state.

## Model boundary

The immutable source is the `X30` MJCF and URDF in the vendor-published [DeepRoboticsLab/deep_robotics_model](https://github.com/DeepRoboticsLab/deep_robotics_model) repository, pinned in `models/model.lock.json`. It supplies the body geometry, collision geometry, inertias, joint limits, meshes, and twelve official leg joints:

`FL/FR/HL/HR_HipX_joint`, `HipY_joint`, and `Knee_joint`.

The source MJCF has no free base or actuator section. The MuJoCo loader creates an in-memory, profile-owned overlay for a free base, floor, visual inspection markers, and bounded motors for those existing joints; vendor files are never changed. The Webots PROTO is regenerated from the same locked URDF and is not claimed to be vendor supplied. The source model does not establish X30 Pro-specific LiDAR, cameras, compute, or payload behavior.

## What the route measures

The inspection lane places two physical blockers in front of the initial X30 pose; the first starts 1.05 m ahead. A measured-state task controller advances through `settle -> evade_first -> pass_first -> evade_second -> pass_second -> goal_hold`: it cannot complete until engine-owned state proves at least 0.85 m body-forward progress, the required lateral detour, physical-course approach and finish crossing with no blocker contact, finite state, safe torso height and safe tilt. Its bounded 34-cycle task command is generated online through the official joints; it is not a written base pose, prerecorded animation, kinematic replay, mocked simulator result, or artificial collision trigger. The source STL bundle has no paint metadata, so the in-memory viewer overlay applies presentation-only mesh colors and hides the source collision solids from the viewer only.

The native simulators have different actuator interfaces, so each has a bounded implementation of the same fixed high-level route. Both derive the terminal result from their own measured base pose and official-joint state.

## Run locally

```powershell
$repo = 'C:\path\to\RoboPay'
$env:PYTHONPATH = "$repo\bridge\deep_robotics\x30_pro_mujoco_bridge"
python "$repo\bridge\deep_robotics\x30_pro_mujoco_bridge\download_x30_model.py"
python "$repo\bridge\deep_robotics\x30_pro_mujoco_bridge\run_inspection_lane.py" --viewer --viewer-hold-seconds 60
```

For the independent simulator comparison, run Webots R2025a and execute:

```powershell
python "$repo\bridge\deep_robotics\x30_pro_mujoco_bridge\run_sim2sim_validation.py"
```

The Windows bridge helper is also compatible with WSL Webots. The `run_sim2sim_validation.py` report is the generated evidence artifact; do not commit a recording or receipt until it was captured from this X30 branch.

## Record the live paid proof on Windows

The recording launcher keeps the MuJoCo viewer native on Windows and runs the real Linux Tunnel through WSL. It pauses before any request so the terminal and viewer can be arranged in a fixed OBS frame. The run then shows discovery, unpaid `402`, the first paid `202` after a clean start, the complete physical action, the correlated `robot/tunnel/result`, execution-gated settlement and BaseScan.

```powershell
$env:PRIVATE_KEY = '<funded Base Sepolia test-wallet key>'
$env:ROBO_PAYEE_ADDRESS = '<payee address>'
.\bridge\deep_robotics\x30_pro_mujoco_bridge\run_live_base_sepolia_visual.ps1
```

The final runner refuses a dirty worktree so the displayed commit identifies the executed source exactly. For a no-payment rehearsal only, use `-DryRun -AllowDirtyWorktree`; it performs discovery and the unpaid `402` but never signs or sends a paid request. The successful paid run writes `artifacts/base_sepolia_result_<timestamp>.json` containing the public terminal status, settlement receipt, canonical course identity and complete correlated simulator-result envelope. Private key material is neither passed to the bridge/Tunnel nor written to the artifact.

## Tunnel contract

The Go Tunnel stays fail closed: `SKILL_CATALOG_PATH` and `ALLOWED_ACTIONS=perform_inspection_gait,stop` must be configured. The bridge accepts only the registered `(robot_id, action, skill_id, params)` tuple, publishes no unknown or parameterized inspection action, and safe `stop` never falls through into motion. Payment verification happens before `PostAction`; settlement happens only after the matching successful terminal result. The shared durable idempotency state blocks payment-bound replays across restarts.

See the registry profile for the canonical catalog, payment policy, and action envelopes.
