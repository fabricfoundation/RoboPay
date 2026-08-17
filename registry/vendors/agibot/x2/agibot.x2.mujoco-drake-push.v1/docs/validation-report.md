# AgiBot X2 — validation report

`agibot.x2.mujoco-drake-push.v1`

Every number below was produced by

```bash
python -m sim_bridge.tools.collect_evidence --sim2sim-cases 10 --json
```

run from `bridge/agibot/x2`, and the raw output is committed alongside this
file as `evidence/collect_evidence.json`. Nothing here is asserted by hand. The
targets are drawn from the same bounds the skill advertises, with a fixed seed,
so re-running reproduces the same task list and a regression shows up as a
changed verdict rather than as a different sample.

## Summary

| Claim | Result |
|---|---|
| Paid action drives the robot end to end | 10 / 10 delivered |
| Sim-to-sim agreement (MuJoCo vs Drake) | 10 / 10 agree |
| Worst puck-end disagreement between engines | 51.1 mm (tolerance 100 mm) |
| Mean puck-end disagreement | 22.3 mm |
| Payment gate rules exercised | 9 / 9 behave as specified |
| Settlement on any failure | never (`settle=false` in all 7 failure cases) |

## 1. Task success across the advertised envelope

Ten target pairs sampled uniformly from the `push_to_target` parameter ranges
(seed 7), rejecting only pairs whose push distance falls outside
`[0.10, 0.17] m`. Success is measured from simulator state — the puck's final
position — not asserted by the policy.

| puck (m) | goal (m) | delivered | final distance | displacement | sim time |
|---|---|---|---|---|---|
| 0.2599, 0.1660 | 0.2745, 0.2922 | yes | 0.0498 | 0.0925 | 2.63 s |
| 0.2630, 0.1746 | 0.2567, 0.3052 | yes | 0.0496 | 0.0860 | 2.95 s |
| 0.2556, 0.1773 | 0.2571, 0.2927 | yes | 0.0467 | 0.0726 | 2.90 s |
| 0.2614, 0.1931 | 0.2587, 0.2967 | yes | 0.0478 | 0.0601 | 2.49 s |
| 0.2644, 0.1979 | 0.2723, 0.3019 | yes | 0.0484 | 0.0737 | 2.46 s |
| 0.2696, 0.1619 | 0.2808, 0.2987 | yes | 0.0476 | 0.0933 | 2.65 s |
| 0.2572, 0.1647 | 0.2643, 0.3145 | yes | 0.0499 | 0.1030 | 3.19 s |
| 0.2577, 0.1833 | 0.2742, 0.3012 | yes | 0.0477 | 0.0919 | 2.53 s |
| 0.2632, 0.1625 | 0.2568, 0.2962 | yes | 0.0493 | 0.0909 | 2.96 s |
| 0.2652, 0.1771 | 0.2644, 0.3076 | yes | 0.0494 | 0.0849 | 2.86 s |

**10 / 10 delivered**, every one inside the 50 mm goal tolerance.

Two things are worth reading off this table rather than the headline. Final
distances cluster tightly at 0.047–0.050 m, right against the tolerance: this
is a pushing mechanism, and where the puck stops is set by friction at the end
of the stroke, not by servo precision. And the envelope is narrow by
construction — see §4.

## 2. Sim-to-sim: MuJoCo against Drake

The same policy object drives both engines. It never touches an engine API: it
consumes an observation and returns joint targets by name, so a disagreement is
attributable to physics rather than to two implementations of the task.

What differs is what the comparison is about — contact resolution, integrator,
and how joints are driven. MuJoCo runs a gravity-compensated PD law over torque
actuators; Drake uses implicit PD actuators solved simultaneously with the
contact problem.

| puck (m) | MuJoCo | Drake | puck-end gap |
|---|---|---|---|
| 0.2599, 0.1660 | ok (0.0498) | ok (0.0480) | 0.0277 |
| 0.2630, 0.1746 | ok (0.0496) | ok (0.0481) | 0.0067 |
| 0.2556, 0.1773 | ok (0.0467) | ok (0.0481) | 0.0225 |
| 0.2614, 0.1931 | ok (0.0478) | ok (0.0496) | 0.0046 |
| 0.2644, 0.1979 | ok (0.0484) | ok (0.0465) | 0.0314 |
| 0.2696, 0.1619 | ok (0.0476) | ok (0.0478) | 0.0298 |
| 0.2572, 0.1647 | ok (0.0499) | ok (0.0496) | 0.0233 |
| 0.2577, 0.1833 | ok (0.0477) | ok (0.0491) | 0.0511 |
| 0.2632, 0.1625 | ok (0.0493) | ok (0.0500) | 0.0025 |
| 0.2652, 0.1771 | ok (0.0494) | ok (0.0491) | 0.0237 |

**Verdicts match on 10 / 10. Worst gap 51.1 mm, mean 22.3 mm, against a 100 mm
tolerance.**

### What had to be true for this to mean anything

Three model-level differences were found and closed. Each of them, left alone,
produced a comparison that looked like a physics result and was not:

1. **The two engines disagreed about which links collide.** Six links carry
   visual-only geometry in AgiBot's MuJoCo scene (`contype=0 conaffinity=0`)
   while the URDF gives them collision tags, and Drake gives every such link a
   convex hull. The hull of `left_wrist_yaw_link` struck the puck at 99.8 N
   partway through the raise and threw it off the table, on a task MuJoCo
   completed. Drake is now filtered to the vendor's own collision set.

2. **The table was a thin slab in Drake and solid in MuJoCo.** The puck
   tunnelled straight through it on contact — leaving at x=0.21, y=0.18, the
   middle of the surface rather than an edge, which is what distinguishes a
   pass-through from a fall. The Drake table now extends to the floor.

3. **The hand was not where the planner thought it was.** See §3; this was the
   substantive one.

The hand is deliberately *not* filtered against the table in either engine, so
both resolve that contact the same way.

## 3. The tool point

The `left_wrist_roll_link` frame origin was being used as the contact point.
Measured against both descriptions, it is not: the hand is a slab about 200 mm
deep hanging *below* that frame.

| | z range in link frame |
|---|---|
| MuJoCo collision mesh | −0.182 … +0.016 |
| Drake collision box | −0.170 … −0.030 |

The frame origin is the hand's **top lip**. Planning to it aimed the hand 10 cm
below every waypoint. MuJoCo still caught the puck with the top edge of its
larger mesh and appeared to work; Drake's smaller box passed underneath and
never touched the puck at all. The sim-to-sim disagreement was not a physics
difference — it was one engine's geometry accidentally covering a planning bug.

The tool point is now `[0, 0, −0.165]`, inside both hulls and near the hand's
lower tip. Driven to 25 mm above the surface, the MuJoCo mesh bottoms out 8 mm
clear of the table and the Drake box 20 mm, and both overlap the side of a
44 mm puck.

This is also why the envelope is narrow: the tool point sits 165 mm below the
wrist, so the travel leg needs the wrist near shoulder height, and
hover-reachable ground shrinks to a band roughly 60 mm wide in x while push
height covers most of the table.

## 4. Actuator limits, and what the planner is allowed to use

The X2 left arm is not uniform. Measured torque limits:

| joint | limit | planned |
|---|---|---|
| left_shoulder_pitch | 36.0 Nm | yes |
| left_shoulder_roll | 36.0 Nm | yes |
| left_shoulder_yaw | 24.0 Nm | yes |
| left_elbow | 24.0 Nm | yes |
| left_wrist_yaw | 24.0 Nm | yes |
| left_wrist_pitch | 2.2 Nm | no |
| left_wrist_roll | 2.2 Nm | no |

The two 2.2 Nm trim joints are excluded from the IK. A plan that spends them is
not executable: asked to roll 2.3 rad, `left_wrist_roll` saturated at 2.2 Nm and
stalled against the hip with an equal and opposite constraint force
(`qfrc_constraint` +2.07 against `qfrc_actuator` −2.20), leaving the tool 37 cm
from a waypoint the solver had called reachable. `left_wrist_pitch` tracks
commands exactly at rest and pins at its limit once the arm extends.

That leaves five load-bearing joints for a five-constraint problem, which is why
rotation about the vertical is left free: it decides only which face of the flat
hand meets a round puck.

## 5. Payment gate

Exercised in-process, no Zenoh required.

| case | status | code | settled |
|---|---|---|---|
| unpaid request | error | `PAYMENT_REQUIRED` | no |
| tampered params | error | `PARAMS_HASH_MISMATCH` | no |
| expired action | error | `ACTION_EXPIRED` | no |
| out-of-range params | error | `PARAMS_OUT_OF_RANGE` | no |
| wrong robot id | error | `UNKNOWN_ROBOT` | no |
| deliberate failure skill | error | `ACTION_FAILED` | no |
| replay of the same key | error | `IDEMPOTENCY_REPLAY` | no |
| free `stop` skill | success | — | yes |
| valid paid action | success | — | yes |

**Settlement is authorised in exactly the two cases that succeeded.** Every
failure path — including a replayed key that was already paid for once —
returns `settle=false`.

The tamper case shifts `goal_x` by +0.02, which is still a legal value inside
the advertised band. It has to be refused for the hash rather than for landing
out of range, or it would prove nothing about tamper detection.

`diagnostic_fail` exists so the no-settle-on-failure guarantee can be
demonstrated on demand rather than argued for.

## 6. Known model artefacts

Recorded because they are properties of the shipped AgiBot model rather than of
this integration, and a reader comparing engines will otherwise trip over them:

- `pelvis` and `hip_pitch` collision geometry interpenetrate by 14.5 mm in the
  model's own rest pose, which MuJoCo resolves with very large contact forces.
  Present on both sides, in every run including all successful ones, and
  outside the working volume. The task neither depends on it nor disturbs it.
- MuJoCo actuates 19 upper-body joints; the URDF Drake parses carries those same
  19 under identical names plus 12 leg joints. Both weld the torso, so the legs
  hang unloaded below the working volume. Every joint that moves is simulated in
  both engines.

## 7. Demo recording

`evidence/push_to_target.mp4` is the simulated action; `push_to_target.log` is
the bridge log for that same run, and `demo-result.json` the request and
result envelopes. All three come from one invocation of
`sim_bridge.tools.record_demo`, so the video and the log describe the same
`actionId` rather than being assembled from separate takes.

The recorded run: `act_4ad818a99603`, puck (0.26, 0.17) to goal (0.27, 0.30),
payment verified, `settle=true`, puck delivered to (0.2682, 0.2551) — 47.9 mm
from the goal, 85.5 mm of displacement, 2 hand contacts peaking at 34.1 N, no
foreign collision.

## 8. Reproducing

```bash
cd bridge/agibot/x2
pytest sim_bridge/tests                                    # 42 tests
python -m sim_bridge.simulation.sim2sim --puck 0.26 0.17 --goal 0.27 0.30
python -m sim_bridge.tools.collect_evidence --sim2sim-cases 10 --json
```
