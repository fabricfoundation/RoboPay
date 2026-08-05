# Validation report — Boston Dynamics Spot obstacle course

Scope: **simulator-only**.  This profile must not be presented as a physical
Boston Dynamics Spot integration.

## Executed evidence (updated 2026-08-04)

| Engine | Result | Evidence |
| --- | --- | --- |
| MuJoCo 3.10 | **passed** | Goal reached at 28.380 s; 3.588 m path length; 0 physical obstacle contacts; minimum root-body-to-obstacle-surface distance 0.543 m. |
| Webots R2025a | **passed** | The actual Cyberbotics Spot PROTO and local Python controller reached the goal at 36.064 s, with 0.568 m minimum root-body-to-obstacle-surface distance; its root never entered the obstacle safety rectangle. |
| Paired sim-to-sim | **passed** | Both results report `spot-obstacle-policy-v2-shared`, identical start pose, goal, route, gait frequency, stabilization period, and steering parameters. |
| x402 Base Sepolia E2E | **passed** | The real Tunnel returned 202, received the correlated Spot success, and settled 0.001 test USDC through the public facilitator: [`0x3361…1717`](https://sepolia.basescan.org/tx/0x3361c185c7c4129bbb3323de86bb11519ba56fa019b259d76ee2e256fefe1717). |

| Robot/skill discovery | **passed** | The real Tunnel exposes the configured robot ID plus `navigate_obstacle_course` and `stop`, enabled state, and 0.001 USDC price before payment; the robot-scoped profile supplies schemas and limits. |
| Bridge parsing/routing | **passed** | Direct tests accept the profile's `skillId` envelope, preserve correlation/payment metadata, cover success/failure, reject invalid speed/route/duration, and prove safe-stop interruption. |
| x402 payment gate | **passed** | The real Tunnel discovers robot/skills/price, then rejects unpaid, malformed, and facilitator-rejected (`isValid: false`) requests with HTTP 402 before an ActionEvent can cross the Zenoh boundary. |
| x402 no-settlement | **passed** | Real Tunnel failure, timeout, payment replay, and post-restart replay scenarios produced zero /settle calls. |

The profile workflow runs the MuJoCo, Webots Sim-to-Sim, payment-gate, and
no-settlement proofs on push/PR. Its Base Sepolia job is manual
`workflow_dispatch` only: a maintainer supplies the two testnet secrets, the
test writes `artifacts/base_sepolia_result_*.json`, and Actions uploads that
public evidence. A normal push never uses the payer key or spends testnet
funds.

The uncommitted, ignored local output files are written to
`bridge/boston_dynamics/spot_mujoco_bridge/artifacts/` when each command is run.
The two result files are produced by the actual installed simulators; neither
engine is substituted or mocked.

The models have different joint-zero conventions and therefore use different
actuator adapters to enact the shared steering signal.  This is an engine
calibration layer, not a second navigation policy: their visual trajectories
need not be frame-for-frame identical.

## Required evidence

| Check | Expected evidence |
| --- | --- |
| MuJoCo policy episode | `artifacts/mujoco_result.json` with `success: true`, zero obstacle contacts, and final goal distance |
| Webots cross-engine episode | `artifacts/webots_result.json` written by the real R2025a controller with `success: true` |
| Paired sim-to-sim identity | `artifacts/sim2sim_result.json` with `shared_policy_match: true` and both engine results successful |
| Base Sepolia paid execution | `test_base_sepolia_tunnel_e2e.py` with a funded test key; it requires HTTP 202, a successful correlated Spot result, and a facilitator settlement transaction |
| Payment gate | `tests/test_payment_gate.py` against the real Tunnel; unpaid, malformed, and facilitator-rejected (`isValid: false`) requests return HTTP 402 before ActionEvent publication |
| Robot and skill discovery | `GET /robot` and `GET /skills`, covered by Go handlers tests and the real-Tunnel payment-gate test |
| Message parsing/action routing | `tests/test_bridge_contract.py`; covers profile `skillId`, correlation metadata, success, failure, invalid contracts, and safe stop |
| Bounded speed | `speedScale` is 0.25..1.0; maximum gait frequency 1 Hz, hip stroke 0.10 rad, knee lift 0.20 rad, steering 0.30 rad |
| Safe stop | active navigation receives correlated `safe_stopped` failure after neutral controls and zero simulated velocity; stop receives correlated success |
| No settlement on failure | `tests/test_x402_no_settlement.py` against the real Tunnel and recording facilitator; failure, timeout, and replay make zero /settle calls |
| Invalid duration | correlated failure before simulator actuation |
| Unknown skill | `UNREGISTERED_ACTION` and no simulator actuation |
| Payment safety | an unsuccessful result is published as `failure`, so the existing tunnel does not settle it |

No presumed success values are committed in advance. A failed Webots episode is
published as a failure by the bridge and therefore must not settle payment.

## Identity limitation

The current shared Gateway/Tunnel protocol identifies robots with `?id=` but
does not define a signed robot-to-payee handshake. Per maintainer guidance,
this profile tracks that binding as an upstream dependency and does not invent
EIP-191. Deployment configuration binds the robot ID and testnet payee, rejects
mismatched action robot IDs, and never exposes a payer private key to the
bridge.
