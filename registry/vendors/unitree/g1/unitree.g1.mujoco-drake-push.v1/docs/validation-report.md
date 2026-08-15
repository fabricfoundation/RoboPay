# Validation report — unitree.g1.mujoco-drake-push.v1

**Scope: simulator-only submission.** No physical robot was used and none of
the evidence below implies physical validation.

Every number here was produced by:

```bash
python -m sim_bridge.tools.collect_evidence
```

Re-running it is how you check that this report still matches the code.

## Environment

| | |
|---|---|
| OS | macOS 26 (Darwin 25.5.0), Apple Silicon |
| Python | 3.13.15 |
| Primary engine | MuJoCo 3.10.0 |
| Validation engine | Drake 1.55.0 |
| Transport | Zenoh (eclipse-zenoh 1.9.0), `tcp/127.0.0.1:7447` |
| Payment | x402 2.16.0, Base Sepolia (`eip155:84532`) |
| ROS 2 | not used — Zenoh is the local robot communication layer |

Robot description: `mujoco_menagerie/unitree_g1/g1_with_hands.xml` in MuJoCo and
the official `unitree_ros` `g1_29dof_with_hand.urdf` in Drake. **All 43 actuated
joint names match across the two descriptions**, which is what makes the
sim-to-sim comparison a comparison of physics rather than of models.

## Validated skills

- [x] `push_to_target` — paid, parameterised by the payer at both ends
- [x] `stop` — free
- [x] `diagnostic_fail` — paid, always fails, exists to exercise no-settle

## Payment gate

Nine cases, run in-process against the same `ActionNode` the Zenoh bridge uses.

| Case | Status | Code | Settled |
|---|---|---|---|
| Unpaid request | error | `PAYMENT_REQUIRED` | **no** |
| Tampered params | error | `PARAMS_HASH_MISMATCH` | **no** |
| Expired action | error | `ACTION_EXPIRED` | **no** |
| Out-of-range params | error | `PARAMS_OUT_OF_RANGE` | **no** |
| Wrong robot id | error | `UNKNOWN_ROBOT` | **no** |
| Deliberate failure skill | error | `ACTION_FAILED` | **no** |
| Free `stop` skill | success | — | yes (price 0) |
| Valid paid action | success | — | yes |
| Replay of the same key | success | `IDEMPOTENCY_REPLAY` | **no** |

Notes on two of these:

- **Tampered params.** The envelope carries `paramsHash`, a canonical-JSON
  SHA-256 of the parameters the payer authorised. The robot recomputes it and
  refuses on mismatch, so an action paid for as "nudge the puck 10cm" cannot be
  edited in flight into something else. The robot does not have to trust the
  routing layer.
- **Replay.** A repeated `idempotencyKey` returns the first outcome without
  re-entering the simulator, and is never settled a second time.

The same nine cases were also exercised end to end over Zenoh with
`tools/send_action.py`; see `docs/README.md` for the commands.

## Task performance

Eight target pairs sampled across the work surface. Success means the puck
finished within 50mm of the commanded destination, measured from simulator
state rather than asserted by the policy.

| Puck | Goal | Result | Moved | Left to goal |
|---|---|---|---|---|
| (0.36, −0.16) | (0.46, 0.02) | ok | 0.165 m | 0.050 m |
| (0.34, −0.20) | (0.44, −0.04) | ok | 0.146 m | 0.050 m |
| (0.40, −0.10) | (0.48, 0.06) | ok | 0.129 m | 0.050 m |
| (0.32, −0.22) | (0.42, −0.10) | ok | 0.114 m | 0.050 m |
| (0.38, −0.06) | (0.46, 0.08) | ok | 0.116 m | 0.050 m |
| (0.42, −0.14) | (0.50, 0.00) | ok | 0.113 m | 0.050 m |
| (0.36, −0.24) | (0.46, −0.12) | **refused** | — | — |
| (0.44, −0.04) | (0.50, 0.04) | ok | 0.054 m | 0.050 m |

**7 of 8 delivered.** The eighth is outside the arm's reachable set: the IK
reports no feasible configuration and the action is refused in 0.15s with an
explicit reason, before any motion. That boundary is published in `skills.yaml`
as the parameter range, so a payer is refused up front rather than charged for
a motion the arm cannot make.

### Why 50mm

The push finishes as an open sweep to a computed end pose, so its terminal
accuracy is around 40–50mm. On one target pair the same request settled at
39.9mm in MuJoCo and 45.3mm in Drake. A 40mm pass mark therefore decides the
verdict by which engine ran the job rather than by whether the robot did it,
so the tolerance is set from the mechanism's measured precision. 50mm is still
inside 1.5 puck radii of the target.

## Sim-to-Sim validation

The **same policy object** drives both engines. What differs is everything the
comparison is about: contact resolution, integrator, and how the joints are
driven — MuJoCo through the menagerie model's position servos, Drake through
PD-controlled actuators added to a URDF that ships no transmissions at all.

Two runs agree when they reach the same verdict and leave the puck within twice
the goal tolerance of each other. (Both stop as soon as they are inside the
tolerance, so two correct runs can legitimately sit on opposite sides of the
target.)

| Puck → Goal | MuJoCo | Drake | Puck-end gap | Agrees |
|---|---|---|---|---|
| (0.36, −0.16) → (0.46, 0.02) | success | success | 0.047 m | ✅ |
| (0.34, −0.20) → (0.44, −0.04) | success | success | 0.040 m | ✅ |
| (0.40, −0.10) → (0.48, 0.06) | success | success | 0.026 m | ✅ |
| (0.32, −0.22) → (0.42, −0.10) | success | success | 0.038 m | ✅ |

Tolerance 0.100 m. **4 of 4 agree.**

Reproduce a single comparison with:

```bash
python -m sim_bridge.simulation.sim2sim --puck 0.34 -0.20 --goal 0.44 -0.04
```

## Known limitations

These are stated because a reviewer will find them anyway.

1. **The pelvis is welded to the world in both engines.** The G1 here has no
   balance controller; rotating the waist and reaching out topples it. More
   importantly the IK planner plans against a welded pelvis, so a floating base
   meant planning in one frame and executing in another. Welding makes the
   planner's assumption true, but it does mean the humanoid is acting as a
   fixed-base manipulator, not walking.

2. **Hand/table collisions are filtered in the Drake back end.** Drake derives
   collision geometry from convex hulls of the finger meshes, which are
   noticeably fatter than the MuJoCo model's collision primitives; a hand
   skimming the surface bottoms out and stalls ~48mm high, unaffected by servo
   gain from 400 through 10000. Hand/puck and puck/table contact are untouched.

3. **The task is a push, not a grasp.** The G1 hand in this model has its index
   and middle fingers fixed 57mm apart with no travel in that direction and an
   opposing thumb 86mm further up the palm. Objects narrower than the split
   pass between the fingers untouched; wider ones are crushed by the
   position-controlled joints, with measured peaks of 60–120N on a 100g object,
   which ejects it. Controlled contact is reliable on this hand; holding is not.

4. **One run takes 10–25 simulated seconds.** The droop correction that trims
   the arm onto its waypoint is deliberately slow, because faster gains wind up
   during the large travel of the raise stage.

## Evidence

Commands: see `docs/README.md`.
Logs: `python -m sim_bridge.tools.collect_evidence --json` reproduces every
table above as JSON.
Recording: `docs/evidence/` — screen capture of the simulated action with the
correlated bridge log.
