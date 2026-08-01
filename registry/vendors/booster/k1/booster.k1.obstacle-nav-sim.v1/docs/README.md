# Booster K1 -- Simulated Obstacle-Avoidance Navigation (Tier 1)

Simulation-only RoboPay integration for the Booster K1 humanoid. A
policy-driven navigation skill is triggered end-to-end from a paid
action over Zenoh, dispatched into MuJoCo, and cross-validated against
an independent Webots simulation running the identical policy code.

## Architecture
Fabric backend -> Tunnel (x402 verify) -> Zenoh (robot/tunnel/action)
-> bridge/booster_k1_zenoh_bridge.py
1. validate envelope + payment (bridge/action_validator.py)
2. check + reserve replay slot (bridge/replay_guard.py)
3. dispatch to MuJoCo (simulation/mujoco/runner.py)
4. publish terminal result (robot/tunnel/result, actionId-correlated)
The policy itself (`simulation/common_policy/dwa_planner.py`, a
Dynamic Window Approach local planner) is imported unchanged by both
the MuJoCo runner and the Webots controller, so the sim-to-sim
comparison is between two physics engines running identical policy
code, not two different policies.

The Booster K1 base is represented as a geometric proxy (cylinder
torso, planar slide+slide+hinge joints) in both simulators -- not an
official Booster K1 CAD model, which is not publicly available. This
is stated explicitly here and in `validation-report.md` rather than
implied.

## Repository layout
robot.profile.yaml, skills.yaml, functions.yaml,
execution-mapping.yaml, payment-policy.yaml Registry contract

bridge/
action_validator.py Payment/security gate (envelope validation)
replay_guard.py SQLite idempotency/replay protection
booster_k1_zenoh_bridge.py Wires the above to Zenoh + the simulator

simulation/
common_policy/dwa_planner.py Shared policy (MuJoCo + Webots both import this)
mujoco/ Scene, runner, results
webots/ PROTO, world, controller, results
sim_to_sim_validate.py Automated comparator (tolerance-based PASS/FAIL)

examples/action-envelope.navigate-to-goal.json Reference action shape
demo/send_test_action.py Manual E2E client
tests/ 36 pytest tests (see validation-report.md)
docs/evidence/ Raw logs from real runs
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

## Running the bridge end-to-end (real Zenoh, real payment gate)

**Terminal A:**
```bash
cd bridge
python3 booster_k1_zenoh_bridge.py
```

**Terminal B:**
```bash
cd demo
python3 send_test_action.py                       # valid paid action -> status=success
python3 send_test_action.py --unpaid               # -> status=rejected, payment_not_verified
python3 send_test_action.py --replay-of <actionId>  # -> status=rejected, replay_detected
```

Raw session log from a real run of all three scenarios is in
`docs/evidence/terminal/bridge-e2e-session.log`.

Note: a freshly-opened Zenoh session needs a few seconds for peer
discovery before its first publish is reliably delivered;
`send_test_action.py` waits 5s before publishing for this reason.

## Running the tests

```bash
pip install -r tests/requirements.txt
python3 -m pytest tests/ -v
```
36 tests across `test_action_validator.py`, `test_replay_guard.py`,
`test_bridge.py`, `test_profile.py`. The simulator and Zenoh session
are mocked in `test_bridge.py`; the real end-to-end wiring is
demonstrated manually per the section above, since it requires a
running Zenoh session and a multi-second physics simulation.
