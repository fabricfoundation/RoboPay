# Deep Robotics M20 Pro — RoboPay Integration (Tier 1)

Simulator-only Tier 1 submission connecting the Fabric RoboPay contract to a
MuJoCo simulation of the Deep Robotics M20 Pro quadruped, via the shared
Zenoh tunnel transport.

## Files

| File | Purpose |
|------|---------|
| `robot.profile.yaml` | Runtime, identity, and kinematic metadata for the M20 Pro simulator profile |
| `skills.yaml` | Discoverable, priced obstacle-navigation skill contract |
| `functions.yaml` | Agent-facing discovery, action, and status functions |
| `payment-policy.yaml` | Base Sepolia USDC policy and result-gated settlement rule |
| `execution-mapping.yaml` | Zenoh-to-MuJoCo mapping and completion semantics |
| `examples/action-envelope.obstacle-nav.json` | Non-production example envelope |
| `bridge/m20_pro_zenoh_bridge.py` | Fail-closed Zenoh/MuJoCo robot adapter |
| `simulation/scenes/m20_pro.xml` | M20 Pro MuJoCo scene (real actuators, obstacles, goal) |
| `simulation/runners/m20_pro_runner.py` | Episode runner: navigation policy + real trot gait + collision detection |
| `demo/run_demo.py` | Bridge-layer demo: well-formed action → success → replay-blocked → stop |
| `demo/run_failure_demo.py` | Deliberate-failure demo: timeout → no settlement |
| `tests/skill-contract.test.yaml` | Human-readable contract cases |
| `tests/test_bridge.py` | Executable parser, replay, result, and settlement-gate tests |
| `simulation/webots/worlds/m20_pro_obstacle_nav.wbt` | Webots world (proxy robot, same obstacle/goal layout) for Sim-to-Sim validation |
| `simulation/webots/controllers/m20_pro_navigation/m20_pro_navigation.py` | Webots controller running the identical navigation policy |
| `simulation/validation/validate_sim_to_sim.py` | Compares MuJoCo vs Webots outcome and writes a PASS/FAIL report |
| `bridge/replay_guard.py` | SQLite-backed replay guard, keyed by `actionId` -- a secondary check at the bridge layer |
| `docs/task-traceability.md` | Maps each bounty requirement to concrete evidence in this repo |
| `docs/evidence/base-sepolia/live-payment-e2e.md` | Live, on-chain, wallet-signed payment proof against a real facilitator |
| `tunnel/pay_m20_pro.py` | Signs and sends a real x402 payment against a running tunnel, for reproducing the live payment test |

## End-to-end architecture

```mermaid
sequenceDiagram
    autonumber
    participant P as Payer / agent
    participant T as Go tunnel (X402VerifyOnly)
    participant Z as Zenoh
    participant A as M20 Pro bridge
    participant S as MuJoCo M20 Pro simulator
    participant W as ExecutionWatcher

    P->>T: POST /action {action, params}
    T->>T: Verify x402 payment against facilitator (never settle here)
    T-->>P: 202 accepted + actionId (or 402 if unverified)
    T->>Z: robot/tunnel/action {actionId, action, params}
    Z->>A: parse_action_event
    A->>A: reject wrong skill / bad params / replayed actionId
    A->>S: run_episode(target_xy, max_episode_steps)
    S-->>A: episode metrics (status, displacement, path length, collisions)
    A->>Z: robot/tunnel/result correlated by actionId
    Z->>W: HandleResult
    alt status == success
        W->>W: ProcessSettlement (real facilitator settle call)
    else error, timeout, rejected
        W->>W: No settlement
    end
    P->>T: GET /action/:id/status
    T-->>P: state, settled
```

Payment verification and settlement now live entirely in the Go tunnel
(`tunnel/internal/handlers`), not in this bridge -- see
`docs/task-traceability.md` for how each requirement maps to the code
that enforces it, and `docs/evidence/base-sepolia/live-payment-e2e.md`
for a real, on-chain proof of this flow.

## Robot spec

| Parameter | Value |
|-----------|-------|
| Type | Quadruped |
| Mass | 18.0 kg |
| Standing height | 0.35 m |
| Forward vx | [0, 1.5] m/s |
| Backward vx | [-1.5, 0] m/s |
| Angular wz | [-1.0, 1.0] rad/s |

## Simulator model

The M20 Pro base moves on a planar (slide-x, slide-y, hinge-yaw) mount
driven every simulation step by a potential-field navigation policy
(velocity actuators). The four legs are independently position-actuated
every step with a live-computed trot gait (gait phase derived from
simulation time, not a pre-recorded animation). Obstacle collisions are
judged with MuJoCo's own narrow-phase contact detection against the base
geometry, not a proximity heuristic — the reported `collisions` metric
reflects genuine physical contact events, separate from the
`avoidance_events` counter (steering reactions while still clear of
contact).

This is a deliberate simplification of full quadruped whole-body balance
control (out of scope for a Tier 1 navigation skill): the base does not
rely on the legs for support, so we avoid needing a full standing/walking
balance controller while still exercising real per-step physics, real
actuation, and real collision detection for the obstacle-avoidance skill.

## Sim-to-Sim validation

The same potential-field navigation policy runs unmodified in two physics
engines -- MuJoCo (primary scene) and Webots (proxy robot body, same
obstacle layout and goal). This is a consistency check on the policy
itself, not a replication of the full leg model: the Webots side uses a
simple rigid body driven by the same policy code, not a 12-DOF quadruped.

```bash
python demo/run_demo.py   # writes docs/evidence/m20_pro_metrics.json
webots --mode=fast --batch simulation/webots/worlds/m20_pro_obstacle_nav.wbt
python simulation/validation/validate_sim_to_sim.py
```

Both engines must reach `goal_reached` with zero collisions, and
displacement/remaining-distance must agree within tolerance. Results are
written to `docs/evidence/sim_to_sim_validation.json`.

## Running locally

```bash
pip install -r tests/requirements.txt
python -m pytest tests/test_bridge.py -v
python demo/run_demo.py
python demo/run_failure_demo.py
```

## Result-gated settlement contract

Settlement (`tunnel/internal/handlers/settlement_watcher.go::ExecutionWatcher`)
is only eligible when a terminal `robot/tunnel/result` reports
`status=success`, which this bridge only publishes when:
- the episode status is `goal_reached`, and
- the collision count (real MuJoCo contacts against obstacles) is `0`.

Timeout, collision, unpaid/unverified payment, malformed events, and
duplicate/replayed actionIds never produce a settlement call --
enforced independently at both the tunnel (`payment_gate.go`,
`settlement_watcher.go`) and the bridge (`replay_guard.py`, this
file's `_on_action`).

## Live payment proof

`docs/evidence/base-sepolia/live-payment-e2e.md` documents a real,
wallet-signed, on-chain Base Sepolia payment gating a real MuJoCo
dispatch, settled only after a genuine success result. To reproduce:

```bash
# terminal 1: bridge
python bridge/m20_pro_zenoh_bridge.py

# terminal 2: tunnel (standalone, real local port)
cd tunnel && go build -o /tmp/localserver ./cmd/localserver
export $(cat .env.local | grep -v '^#' | xargs)
/tmp/localserver --port=$LOCALSERVER_PORT --payto=$PAYEE_ADDRESS \
  --network=$NETWORK --price=\$$PRICE --facilitator=$FACILITATOR_URL \
  --allowed-actions=$ALLOWED_ACTIONS

# terminal 3: sign and send a real payment
cd tunnel && python pay_m20_pro.py
```
