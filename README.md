# Reachy Mini · Tier 1 — Sim-to-Sim + Payment-Gated Robot Action

A paid RoboPay action starts a closed-loop object-tracking episode on the official Hugging Face Reachy Mini model (9 DOF) and validates the behavior independently across MuJoCo and Webots physics engines.

```text
x402 paid request (Base Sepolia USDC)
  → Fabric Gateway (wss://api.fabric.foundation)
  → RoboPay Tunnel (Go binary, x402 middleware)
  → Zenoh robot/tunnel/action
  → Reachy Mini bridge (Python)
  → FSM: SCANNING → TRACKING → EXPRESSIVE
  → MuJoCo physics (official MJCF) + Webots physics (URDF-derived, 9 DOF)
  → correlated robot/tunnel/result {status: "success"}
  → x402 settlement ONLY after simulator SUCCESS
```

---

## Simulation Task

The Reachy Mini has no arms. The implemented skill is an **expressive head-tracking task**: a paid action such as `look_at_apple` causes the controller to track the requested object using:

- Torso yaw (1 DOF)
- Stewart neck platform (6 DOF)
- Two antenna actuators (2 DOF)

The policy (`ReachyTaskPolicy`) reads the live simulator state (`head_xmat`, joint positions, target position) at every 5 ms step and computes the control command dynamically via a finite-state machine with P-control and slew-rate limiting. **No recorded trajectory or predefined animation is replayed.**

### Actuator layout (matches official MJCF)

| Index | Joint | Range (rad) | Function |
|---:|---|---|---|
| 0 | `yaw_body` | [-2.79, +2.79] | Torso rotation (±160°) |
| 1 | `stewart_1` | [-0.84, +1.40] | Neck leg 1 |
| 2 | `stewart_2` | [-1.40, +1.22] | Neck leg 2 |
| 3 | `stewart_3` | [-0.84, +1.40] | Neck leg 3 |
| 4 | `stewart_4` | [-1.40, +0.84] | Neck leg 4 |
| 5 | `stewart_5` | [-1.22, +1.40] | Neck leg 5 |
| 6 | `stewart_6` | [-1.40, +0.84] | Neck leg 6 |
| 7 | `right_antenna` | [-0.80, +0.80] | Expressive |
| 8 | `left_antenna` | [-0.80, +0.80] | Expressive |

Joint limits are derived from the official Reachy Mini URDF and match the MuJoCo MJCF exactly.

---

## Sim-to-Sim Validation

The **same** `ReachyTaskPolicy` class is evaluated independently in two physics engines:

- **MuJoCo** — official MJCF from the `reachy_mini` pip package, Euler solver
- **Webots** — URDF-derived PROTO (R2025a), ODE solver, launched as a real subprocess (`--batch --mode=fast --no-rendering`)

| Target | Simulator | Tracked | Success Rate | Min Error | Duration |
|---|---|---:|---:|---:|---:|
| Apple | MuJoCo | ✅ | 1.0 | 0.140 rad | 3.01 s |
| Croissant | MuJoCo | ✅ | 1.0 | 0.163 rad | 3.01 s |
| Duck | MuJoCo | ✅ | 1.0 | 0.147 rad | 3.01 s |
| Apple | Webots | ✅ | 1.0 | 0.340 rad | 12.00 s |
| Croissant | Webots | ✅ | 1.0 | 0.196 rad | 12.00 s |
| Duck | Webots | ✅ | 1.0 | 0.473 rad | 12.00 s |

**Overall sim-to-sim robustness score: 1.0**
**`webots_validation_passed: true`**

The different error values between engines (e.g., Apple: 0.140 vs 0.340 rad) prove independent physics computation — not a shared mock.

---

## Payment Integration (x402 · Base Sepolia)

### Execution-gated settlement

```text
POST /action (no payment)     → HTTP 402 + PAYMENT-REQUIRED header
POST /action (signed USDC)    → x402 facilitator verifies + settles on-chain
                              → Tunnel publishes robot/tunnel/action (Zenoh)
                              → Simulator executes (MuJoCo + Webots sim2sim)
                              → robot/tunnel/result {status: "success"}
                              → Tunnel returns HTTP 200
                              → Settlement confirmed ONLY after SUCCESS
```

Simulator failure → HTTP 502 (no settlement). Timeout → HTTP 504 (no settlement).

### External endpoints

| Component | Endpoint |
|---|---|
| Fabric public action API | `https://api.fabric.foundation/api/core/robots/reachy-mini-kauker/action` |
| Fabric Gateway → Tunnel | `wss://api.fabric.foundation/api/core/ws/robot` |
| x402 facilitator | `https://x402.org/facilitator` |
| Action topic (Zenoh) | `robot/tunnel/action` |
| Result topic (Zenoh) | `robot/tunnel/result` |
| Metrics topic (Zenoh) | `robot/reachy_mini/metrics` |
| Network | `eip155:84532` (Base Sepolia) |
| Asset | USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` |
| Payee | `0x39a315667d557B1425bb1e5D371DD66d300c98c1` |

### Live Base Sepolia evidence (4 transactions)

| # | Transaction | Status |
|---|---|---|
| 1 | [`0x49f9b6e4...`](https://sepolia.basescan.org/tx/0x49f9b6e4111774a85a20adfe0aaa9633be33872ea7062b0127b64483ceb13d74) | ✅ Confirmed |
| 2 | [`0x5bdba3a0...`](https://sepolia.basescan.org/tx/0x5bdba3a0e3af61b76bab1c9da973f8902694e42e93cf1124ddf9424460091421) | ✅ Confirmed |
| 3 | [`0xd322cd3d...`](https://sepolia.basescan.org/tx/0xd322cd3d06a03b57aa4da8c3277a70d3daa52f1397324a31c00332a34e7799e6) | ✅ Confirmed |
| 4 | [`0xc5664590...`](https://sepolia.basescan.org/tx/0xc566459096236f33d541c9d6db340be452132677f11c22fcdfe565943a5bf9b9) | ✅ Confirmed |

All transactions are `transferWithAuthorization` calls on the Base Sepolia USDC contract, verifiable by any reviewer.

### Safety guarantees

| Protection | HTTP Code | Behavior |
|---|---|---|
| Unpaid/invalid request | 402 | Returns x402 requirements, no ActionEvent published |
| Replayed action ID | 409 | `REPLAY_DETECTED`, idempotency key with 10-min TTL |
| Disallowed skill | 403 | `SKILL_NOT_ALLOWED` via `ALLOWED_ACTIONS` allowlist |
| Duration exceeded | 400 | `DURATION_LIMIT` via `MAX_ACTION_DURATION_SECONDS` |
| Rate limit exceeded | 429 | `RATE_LIMITED`, 60 RPM per IP (configurable) |
| Simulator failure | 502 | `SIMULATOR_EXECUTION_FAILED`, no settlement |
| Simulator timeout | 504 | `SIMULATOR_RESULT_TIMEOUT` (90s), no settlement |

---

## CI/CD (GitHub Actions)

### Automated CI (`.github/workflows/ci.yml`)

Triggers on push/PR to `feat/reachy-mini-tier-1`:

```yaml
jobs:
  tunnel-build-test:    # Go 1.25, zenoh-c 1.9.0, make build + make test
  sim-validation:       # Python 3.10, MuJoCo, sim2sim (Webots skipped on GH runners)
```

### Manual E2E (`.github/workflows/base-sepolia-e2e.yml`)

`workflow_dispatch` with secrets:

```yaml
secrets:
  BASE_SEPOLIA_PRIVATE_KEY   # Payer wallet (never in code)
  ROBO_PAYEE_ADDRESS         # Robot payee wallet
inputs:
  robot_id: "reachy-mini-kauker"
```

Produces 90-day retention artifacts: `base_sepolia_result*.json`, `webots_sim2sim_result.json`.

---

## Project Structure (useful files only)

```text
bridge/reachy_mini/
├── mujoco_sim_bridge/
│   ├── main.py                          # Bridge entrypoint (Zenoh listener)
│   ├── policy/
│   │   └── controller.py                # ReachyTaskPolicy FSM (shared MuJoCo+Webots)
│   ├── simulation/
│   │   ├── environment.py               # MuJoCo env (official MJCF)
│   │   ├── metrics.py                   # Angular error tracker
│   │   ├── sim2sim.py                   # Sim2SimValidator (no fallback, honest reporting)
│   │   ├── webots_env.py                # DISABLED (raises RuntimeError)
│   │   └── scenes/
│   │       ├── reachy_mini_simple.wbt   # Webots scene (URDF model, 9 DOF)
│   │       ├── protos/
│   │       │   ├── reachy_mini_simple.proto  # URDF-derived PROTO
│   │       │   └── assets/              # 41 STL meshes (repo-relative)
│   │       └── controllers/
│   │           └── reachy_mini_controller/
│   │               └── reachy_mini_controller.py  # Webots native controller
│   └── reachy_mini/
│       └── node.py                      # Bridge node (action → sim → metrics)
├── test_base_sepolia_tunnel_e2e.py      # Live E2E (Fabric + x402 + sim)
├── test_e2e_paid_action.py              # Local E2E (proxy + facilitator)
└── test_payment_gate.py                 # Payment gate vs real Go binary

tunnel/
├── cmd/main.go                          # Go tunnel entrypoint
├── config/config.go                     # Configuration loader
├── internal/
│   ├── client.go                        # Fabric WebSocket client
│   ├── handlers/handlers.go             # Action handler + execution gate
│   └── aipagent/agent.go               # AIP A2A agent
├── go.mod                               # x402-foundation/x402/go, zenoh-go, gin
└── go.sum

.github/workflows/
├── ci.yml                               # Automated build + test
└── base-sepolia-e2e.yml                 # Manual live on-chain test

Makefile                                  # build, test, download-zenohc, bridge-*
```

---

## Requirements

### Build

| Dependency | Version | Purpose |
|---|---|---|
| Go | ≥ 1.25 | Tunnel binary |
| zenoh-c | 1.9.0 | Auto-downloaded by `make download-zenohc` |
| Python | ≥ 3.10 | Bridge + tests |
| MuJoCo | ≥ 3.0 | Primary physics engine |
| Webots | ≥ R2023b | Second physics engine (sim2sim) |
| reachy_mini (pip) | latest | Official MJCF + URDF model |

### Python packages

```text
mujoco
zenoh
requests
eth_account
x402
reachy_mini
```

### Environment variables (live test only — never committed)

```bash
export PRIVATE_KEY=0x...              # Base Sepolia payer wallet
export ROBOT_ID=reachy-mini-kauker
export ROBO_PAYEE_ADDRESS=0x39a315667d557B1425bb1e5D371DD66d300c98c1
```

---

## Reproduce Locally

```bash
# 1. Build + unit tests
make build
make test

# 2. Local E2E (no real funds needed)
python3 bridge/reachy_mini/test_e2e_paid_action.py

# 3. Live Base Sepolia E2E (needs testnet USDC)
export PRIVATE_KEY=0xYOUR_LOCAL_TEST_KEY
export ROBOT_ID=reachy-mini-kauker
export ROBO_PAYEE_ADDRESS=0xYOUR_PAYEE_ADDRESS
python3 bridge/reachy_mini/test_base_sepolia_tunnel_e2e.py
```

### Test results (local, 2026-07-23)

| Test | Result | Environment |
|---|---|---|
| `make build` | ✅ OK | WSL Ubuntu 22.04, Go 1.25, zenoh-c 1.9.0 |
| `make test` (12 Go tests) | ✅ ALL PASS | WSL Ubuntu 22.04 |
| `test_e2e_paid_action.py` | ✅ OK | WSL (local proxy + facilitator) |
| `test_base_sepolia_tunnel_e2e.py` | ✅ OK (4×) | WSL → public Fabric Gateway → Base Sepolia |
| Sim2Sim standalone | ✅ Score 1.0 | WSL (MuJoCo 3.3 + Webots R2025a) |
| Webots GUI validation | ✅ Robot tracks | Windows (visual confirmation) |

---

## Security Hardening (production)

```bash
export ALLOWED_ACTIONS=look_at,look_at_apple,inspect_table
export MAX_ACTION_DURATION_SECONDS=30
export ACTION_RATE_LIMIT_RPM=60
```

Payment verification alone does not grant unrestricted robot control. The allowlist, duration cap, rate limit, replay protection, and execution-gated settlement all apply independently.

---

*No private keys or secrets are included in this PR.*
