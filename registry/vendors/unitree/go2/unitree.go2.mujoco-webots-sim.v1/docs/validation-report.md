# Validation report — unitree.go2.mujoco-webots-sim.v1

OS: macOS 15 (arm64); MuJoCo tests also on ubuntu-latest via CI
ROS2: not used (simulator-only; Zenoh consumed directly, see simulation/README.md)
Zenoh: eclipse-zenoh 1.x (Python) + zenoh-c 1.9.0 (tunnel), peer mode

## Validated skills

- [x] navigate_to

## Validation results

- [x] Skill catalog returns expected skills (robopay_link.py startup log)
- [x] Unpaid request returns 402 (simulation/go2/test_payment_gate.py)
- [x] Paid request returns 200 accepted (tunnel PostAction; test_link.py)
- [x] Duplicate idempotencyKey does not execute twice (test_result_semantics.py)
- [x] Zenoh message received (test_link.py: tunnel round-trip + action delivery)
- [x] Robot bridge received action (robopay_link.py logs with actionId)
- [x] Robot movement observed (MuJoCo/Webots episodes; simulation/docs/go2_nav.gif)
- [x] Structured result on robot/tunnel/result correlated by actionId
- [x] Failure paths return {"status": "error"} and never settle
      (NO_PATH / INVALID_PARAMS / UNKNOWN_SKILL / DUPLICATE / tampered
      paramsHash — test_result_semantics.py)

## Evidence

Commands:

    cd simulation && ./setup.sh && cd .. && make build
    cd simulation/go2
    python3 test_payment_gate.py
    python3 test_result_semantics.py
    python3 test_link.py

Logs: each test prints its checks as JSON and PASS/FAIL; tunnel logs land
in /tmp/tunnel_*.log.

Known limitations: simulator-only profile — payment settlement is
simulated (the x402 402 gate is exercised against the real tunnel; no
on-chain settlement happens). Safe stop: the gait controller commands a
stand posture whenever the velocity command is zero; episodes are bounded
by a 90 s timeout.
