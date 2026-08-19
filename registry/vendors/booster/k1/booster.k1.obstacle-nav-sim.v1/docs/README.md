# Booster K1 -- Simulated Obstacle-Avoidance Navigation (Tier 1)

Simulation-only RoboPay integration for the Booster K1 humanoid. A
policy-driven navigation skill is triggered end-to-end from a paid
action, gated by a fail-closed x402 payment flow in the Go tunnel,
dispatched into MuJoCo, and cross-validated against an independent
Webots simulation running the identical policy code.

## Architecture
Client -> POST /action (PAYMENT-SIGNATURE header)
-> tunnel/internal/handlers: X402VerifyOnly (verify only, never settle here)
-> PostAction: fail-closed skill allowlist check, reserve actionId
(durable, restart-surviving idempotency store), publish to
robot/tunnel/action, return 202 {actionId, status_url}
-> bridge/booster_k1_zenoh_bridge.py: parse event (shared
action_event.py), reject wrong skill / bad params / replayed
actionId, dispatch simulation/mujoco/runner.py
-> publish terminal result to robot/tunnel/result (actionId-correlated)
-> tunnel/internal/handlers: ExecutionWatcher consumes the result;
settles (calls the x402 facilitator) ONLY if status=success,
exactly once per actionId
-> client polls GET /action/:id/status for the terminal state
Payment verification and settlement are handled entirely in the Go
tunnel. The Python bridge does not see, validate, or re-verify any
payment field -- by the time an event reaches
`robot/tunnel/action`, it has already passed the tunnel's fail-closed
gate. The bridge's own responsibility is narrower: don't dispatch a
malformed event, don't dispatch a replayed `actionId`, and report a
truthful result so the tunnel knows whether to settle.

The policy itself (`simulation/common_policy/dwa_planner.py`, a
Dynamic Window Approach local planner) is imported unchanged by both
the MuJoCo runner and the Webots controller, so the sim-to-sim
comparison is between two physics engines running identical policy
code, not two different policies.

The Booster K1 base is represented as a geometric proxy (cylinder
torso, planar slide+slide+hinge joints) in both simulators -- not an
official Booster K1 CAD model, which this submission does not have
access to. This is stated explicitly here and in
`validation-report.md` rather than implied.

## Repository layout
robot.profile.yaml, skills.yaml, functions.yaml,
execution-mapping.yaml, payment-policy.yaml Registry contract
(payment enforcement now
points at the tunnel Go
files via enforcedBy)

bridge/
booster_k1_zenoh_bridge.py Parses robot/tunnel/action (shared
action_event.py), replay guard, dispatches
to the simulator, publishes the result
replay_guard.py SQLite bridge-local dedup (belt-and-braces;
the tunnel's idempotency store is authoritative)

simulation/
common_policy/dwa_planner.py Shared policy (MuJoCo + Webots both import this)
mujoco/ Scene, runner, results
webots/ PROTO, world, controller, results
sim_to_sim_validate.py Automated comparator (tolerance-based PASS/FAIL)

examples/action-envelope.navigate-to-goal.json Reference action shape
(payment fields shown
are the HTTP-layer
request, not what
reaches Zenoh)
tests/ 27 pytest tests (Python side)
docs/evidence/ Raw logs from real runs

../../../../../../tunnel/ Go tunnel (shared across
all robots in this repo)
internal/handlers/
handlers.go PostAction: fail-closed allowlist + async
202/status contract
idempotency.go Durable (file-backed) replay guard,
survives a tunnel restart
payment_gate.go X402VerifyOnly: verifies, never settles
settlement_watcher.go ExecutionWatcher: the ONLY code path that
settles, and only after a real success result
cmd/e2e_test.go End-to-end proof (fake facilitator) that
settlement happens exactly once, only
after success, never on replay
## Reproducing the MuJoCo run

```bash
cd simulation/mujoco
python3 runner.py --goal_x 5.0 --goal_y 0.0 --max_time_sec 60
```
Writes `simulation/mujoco/results/metrics.json`. Expected: `status: success`,
`distance_to_goal_m` under 0.3, `collision_count: 0`.

## Reproducing the Webots run

Webots (tested on R2025a, snap package) runs controllers inside its
own sandboxed Python 3.10, which cannot have packages installed into
it. We use Webots' **extern controller** mode instead: the world's
robot controller is set to `<extern>`, and the real controller script
runs as a normal process using whichever Python environment has
`numpy`/`mujoco` installed.

Two terminals:

**Terminal A -- start Webots, which will wait for the extern controller:**
```bash
cd simulation/webots
webots --batch --mode=fast --minimize --stdout --stderr worlds/booster_k1_obstacle_nav.wbt
```
Wait for: `INFO: 'k1_base' extern controller: Waiting for local or remote connection on port 1234...`

**Terminal B -- run the actual controller:**
```bash
export WEBOTS_HOME=/snap/webots/current/usr/share/webots   # adjust to your install
export PYTHONPATH=$WEBOTS_HOME/lib/controller/python:$PYTHONPATH
export LD_LIBRARY_PATH=$WEBOTS_HOME/lib/controller:$LD_LIBRARY_PATH
export WEBOTS_CONTROLLER_URL=ipc://1234/k1_base

cd simulation/webots/controllers/k1_navigation
GOAL_X=5.0 GOAL_Y=0.0 MAX_TIME_SEC=60 python3 k1_navigation.py
```
Writes `simulation/webots/results/metrics.json`. Expected: same shape
of result as MuJoCo, `status: success`.

## Sim-to-sim validation

After both `results/metrics.json` files exist:
```bash
python3 simulation/sim_to_sim_validate.py --skip-run
```
Compares `distance_to_goal_m` (abs tolerance 0.15m), `path_length_m`
(relative tolerance 15%), `collision_count` and `status` (exact
match). Verified result: PASSED, with under 2% divergence between
the two engines on the reference scenario.

## Running the Go tunnel tests (payment gate, idempotency, deferred settlement)

Requires the `zenoh-c` C library (Cgo dependency of the `zenoh-go`
binding):

```bash
curl -fsSL -o /tmp/zc.zip \
  "https://github.com/eclipse-zenoh/zenoh-c/releases/download/1.9.0/zenoh-c-1.9.0-x86_64-unknown-linux-gnu-standalone.zip"
unzip -q /tmp/zc.zip -d /tmp/zenoh-c

export CGO_CFLAGS="-I/tmp/zenoh-c/include"
export CGO_LDFLAGS="-L/tmp/zenoh-c/lib -lzenohc"
export LD_LIBRARY_PATH="/tmp/zenoh-c/lib:$LD_LIBRARY_PATH"

cd ../../../../../../tunnel   # from this profile dir, or just `cd tunnel` from repo root
go build ./...
go vet ./...
go test ./... -v
```

24 tests across `internal` (pre-existing WS client tests, untouched),
`internal/handlers` (fail-closed allowlist, idempotency
persistence/restart-survival, verify-only gate, execution watcher --
21 tests), and `cmd` (3 end-to-end tests against a fake/recording
facilitator, proving: an unpaid request never reaches the facilitator
at all; a genuine success result settles exactly once; a failure
result, a settlement failure, and a replayed success result all never
cause an unwarranted or duplicate settlement).

Constructing a real EVM-signed `PAYMENT-SIGNATURE` header (which would
require a private key and EIP-712 signing client) and a live Base
Sepolia transaction are both out of scope for this environment; the
verify/settle separation itself is proven directly against the same
`ExecutionWatcher` and `IdempotencyStore` types production code uses.

## Running the Python tests

```bash
pip install -r tests/requirements.txt
python3 -m pytest tests/ -v
```
27 tests across `test_bridge.py` (event parsing, wrong skill, bad
params, replay, simulator failure/collision -- payment is out of
scope here since the bridge never sees payment data), `test_profile.py`
(registry YAML cross-consistency), and `test_replay_guard.py`
(bridge-local SQLite dedup).
