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
