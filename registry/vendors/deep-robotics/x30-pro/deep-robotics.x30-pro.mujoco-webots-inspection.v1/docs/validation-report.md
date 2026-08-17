# Validation report — Deep Robotics X30 Pro Tier 1

This report distinguishes reproducible simulator/contract validation from the
live-chain receipt and operator recording that must be captured from the final
submitted commit.

## Reproducible validation

| Boundary | Required observation | Current result |
| --- | --- | --- |
| Official model | Pinned vendor X30 MJCF and URDF with verified checksums | Pass |
| MuJoCo | Real joint-actuated, measured-state task episode with finish crossing and zero blocker contact | Pass: 1.12128 m body-forward progress, 0.79497 m minimum approach clearance, 0.39734 m minimum torso height, 0.49346 rad maximum tilt |
| Webots R2025a | Generated from the same pinned vendor URDF; independent physics and measured-state task result | Pass: 0.89246 m body-forward progress, 0.17855 m minimum approach clearance, 0.45868 m minimum height, 0.40102 rad maximum tilt |
| Sim-to-Sim | Both engines satisfy `x30-pro-two-obstacle-slow-slalom-v5` (`6f57922dddf966288c4f44c1b7eba04a92f7168b226127136509cadd27b15b78`) | Pass: every acceptance check true in both engines |
| Invalid facilitator verdict | `isValid:false` returns 402 before Zenoh publication | Pass: zero ActionEvents, simulator results/state changes and settlements |
| First paid action | No warm-up; immediate 202/pending, one correlated MuJoCo success and one settlement | Pass in required local real-Tunnel harness |
| Negative execution | Injected real-Zenoh failure and timeout remain unsettled | Pass |
| Replay | Same action, payment fingerprint and action after restart are rejected without second dispatch | Pass |
| WebSocket transport | Continuation frames are reassembled before decoding the first response | Pass |

Run the same checks with:

```bash
make build
make test
python bridge/deep_robotics/x30_pro_mujoco_bridge/download_x30_model.py
export PYTHONPATH="$PWD/bridge/deep_robotics/x30_pro_mujoco_bridge"
export TUNNEL_BIN="$PWD/bin/tunnel"
export LD_LIBRARY_PATH="$PWD/.zenoh-c/lib"
python -m unittest discover -s bridge/deep_robotics/x30_pro_mujoco_bridge/tests -p 'test_*.py' -v
WEBOTS_EXE=/usr/local/bin/webots python bridge/deep_robotics/x30_pro_mujoco_bridge/run_sim2sim_validation.py
```

## Final evidence status

- [x] MuJoCo engine-owned result generated.
- [x] Webots engine-owned result generated.
- [x] Real Sim-to-Sim report generated from the two independent executions.
- [x] Invalid-payment, failure, timeout, replay and cold-start positive paths covered by mandatory tests.
- [x] Trusted Base Sepolia receipt captured from source commit `3919acb5a430a240d7b6c50c7a0ec906d1d82265`.
- [x] Continuous split-screen operator recording captured from that same runtime source.
- [x] Commit SHA, action ID, transaction hash, recording SHA-256, trusted JSON artifact and stable URLs bound in `docs/evidence/evidence-manifest.yaml`.

The live run used action `x30-drive-1786994629` and settled in Base Sepolia
transaction `0x52c1bcd466025d475740a72d7b9237ff15bef4fef943ff38be3b91aef8f42ff6`.
The evidence-only follow-up commit adds the byte-identical recording and JSON;
it does not modify the recorded runtime source.
