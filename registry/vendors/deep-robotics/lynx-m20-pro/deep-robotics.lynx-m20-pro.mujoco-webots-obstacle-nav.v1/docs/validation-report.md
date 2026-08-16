# Validation report template — Lynx M20 Pro

This file documents the checks a CI run must produce; it is intentionally not
presented as a substituted live-chain receipt.

| Boundary | Required observation |
| --- | --- |
| Invalid facilitator verdict | `isValid:false` returns 402; zero ActionEvents, state changes, results/metrics and settlements |
| First paid action | immediate 202/pending, one correlated vendor-MJCF MuJoCo obstacle detection/yield/release/goal success, one settlement |
| Negative execution | injected real-Zenoh failure and timeout remain unsettled |
| Replay | same action, same payment fingerprint, and same action after restart are rejected without second dispatch |
| Sim-to-Sim | MuJoCo and Webots R2025a each report physical obstacle detection, yield duration, release, no collision, measured goal displacement and safe base state |
| Live network | trusted Base Sepolia job uploads receipt/result JSON generated in that run |

The checked source, checksums, parameter bounds, topics and correlation tuple
are declared in the adjacent registry YAML/JSON files and bridge runbook.

## Current visual and live-chain evidence

The continuous 52.47-second operator recording is bound to source commit
`7928cdbc149ea9b3581ffa19b276ebaf9158b54f`, action ID
`m20-drive-1786908727`, and trusted artifact
`bridge/deep_robotics/m20_pro_mujoco_bridge/artifacts/base_sepolia_result_1786908768.json`.
It keeps the terminal and MuJoCo viewer together while showing public Fabric
Gateway discovery, unpaid `HTTP 402`, first paid `HTTP 202`, the physical
obstacle detection/yield/release/resume sequence, measured goal completion,
correlated `succeeded`, execution-gated `settled: true`, and the matching
BaseScan page.

- Base Sepolia transaction:
  `0x48b77ee0f88ee35d255d20a410d03867cc950d8dfcab9511bfaeeae180e65213`
- Recording SHA-256:
  `d4656640f02defdb7618aed37582b988f2401977afaadfb9d72c52eff1095318`
- Recording:
  https://github.com/user-attachments/assets/99bb9334-c775-436a-88a7-d2825eb6a1d8
