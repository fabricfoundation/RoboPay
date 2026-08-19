# Validation report — Atlas Tier 1 shelf inspection

Profile: `boston-dynamics.atlas.mujoco-pybullet-webots-shelf-inspection.v1`
Skill: `inspect_shelf` · Policy: `atlas-shelf-inspection-dls-v1`

Every number below is produced by a command in this repository and written to
[`docs/evidence/`](../../../../../../docs/evidence). Nothing is transcribed by
hand.

Skill discovery and pricing are published in
[`skill-catalog.json`](../skill-catalog.json), generated from `skills.yaml` so
the two cannot disagree.

## 1. Evidence ladder

The claims are built up in layers, so a reviewer can see exactly which rung each
one rests on.

| Rung | What it establishes | Artefact |
| --- | --- | --- |
| Model integrity | The robot is the pinned Atlas v4 and the code addresses its actuators correctly | `tests/test_model_integrity.py` |
| Kinematics | The shared URDF Jacobian agrees with MuJoCo's own | `tests/test_kinematics.py` |
| Reach envelope | Where free-standing Atlas can reach without losing balance | `reach-envelope.json` |
| Free-standing task | The full inspection sequence succeeds on its own feet | `mujoco-inspection-episode.json` |
| Cross-simulator | The same robot, task and controller agree on two further engines | `pybullet-inspection-episode.json`, `webots-inspection-episode.json`, `sim2sim-validation.json` |
| Payment gate | Execution is gated by x402 and settles only on success | `demo-e2e-evidence.json` |
| Tunnel flow | A payment-validated action reaches the robot over the real Zenoh transport | `tunnel-e2e-evidence.json` |
| Facilitator | A forged authorization is refused by the live x402 facilitator | `tunnel-e2e-evidence.json`, `tests/test_facilitator.py` |
| Real Go tunnel | The repository's own tunnel refuses unpaid and forged actions | `go-tunnel-e2e-evidence.json` |
| Idempotency | A payment-validated action actuates the robot once, across restarts | `tests/test_idempotency.py` |
| On-chain settlement | A settlement of this skill really happened on Base Sepolia | `onchain-settlement.json` |

## 2. Model integrity

Atlas v4 is fetched, never vendored:

| Field | Value |
| --- | --- |
| Upstream | `openai/roboschool` |
| Commit | `d32bcb2b35b94168b5ce27233ca62f3c8678886f` |
| File | `roboschool/models_robot/atlas_description/urdf/atlas_v4_with_multisense.urdf` |
| License | MIT |
| Mesh assets committed | none — upstream collision geometry is analytic |

Actuator addressing is read back out of the compiled model and checked against
the URDF's own effort limits (890 N·m knee, 840 N·m hip pitch, 112 N·m elbow).
A mismatch in the joint set or in any effort limit raises immediately;
`test_actuator_validation_fails_loudly_on_drift` pins that behaviour.

## 3. Reach envelope

`python -m bridge.boston_dynamics.atlas_bridge.reach_envelope` drives the arm to
a 6 × 6 grid of offsets from the settled home pose and records, per probe,
whether the arm converged to 30 mm and whether Atlas was still standing.

```
vert \ fwd   +0.06  +0.12  +0.18  +0.21  +0.24  +0.30
  +0.20       OK     OK     OK     OK    fall   miss
  +0.10       OK     OK     OK    miss   fall   fall
  +0.00       OK     OK     OK     OK     OK     OK
  -0.06       OK     OK     OK     OK     OK     OK
  -0.12       OK     OK     OK     OK    fall   fall
  -0.20      fall   fall   fall   fall   fall   fall
```

23 of 36 probes are usable. The reported envelope is the **largest block in
which every probe succeeded** — forward 0.06–0.18 m, vertical −0.12 to +0.20 m
(15/15) — not a bounding box around scattered successes.

The three inspection targets sit at 0.13–0.15 m forward and −0.06 to +0.06 m
vertical, inside that block. `test_targets_stay_inside_the_validated_reach_core`
fails if a target is ever moved out of it.

## 4. Free-standing task result

Atlas is never welded, clamped or supported. The fall threshold is 0.70 m of
pelvis height — it stands at 0.911 m — rather than a floor-contact test.

| Metric | MuJoCo | PyBullet | Webots R2025a |
| --- | --- | --- | --- |
| Status | success | success | success |
| Targets reached and held | 3 / 3 | 3 / 3 | 3 / 3 |
| Mean end-effector error | 9.54 mm | 12.18 mm | 8.98 mm |
| Max end-effector error | 13.49 mm | 19.79 mm | 12.33 mm |
| Min pelvis height | 0.9084 m | 0.9395 m | 0.8982 m |
| Fall detected | no | no | no |
| Shelf collisions | 0 | 0 | 0 |
| Episode duration | 4.61 s | 4.98 s | 7.70 s |
| Completion reason | sequence_complete | sequence_complete | sequence_complete |

Per-target accuracy is recorded individually in each episode JSON.

MuJoCo runs are bit-identical across repeats (`test_run_is_repeatable`), so the
repeatability claim is checked rather than asserted.

## 5. Sim-to-sim

`sim2sim-validation.json` runs the task on every available engine and compares
them. One pinned URDF drives all three; the Jacobian comes from that URDF rather
than from any engine, so the controller is identical everywhere. What differs is
the physics engine and the joint servo: MuJoCo applies an explicit PD law with a
gravity feedforward, while PyBullet and Webots apply joint-position commands
saturated at the same URDF effort limits.

| Check | Limit | Measured |
| --- | --- | --- |
| Every engine completed every target | required | yes |
| No engine reported a fall | required | yes |
| No engine reported a shelf collision | required | yes |
| Mean-error spread across the three engines | ≤ 50 mm | 3.20 mm |
| Duration spread across the three engines | ≤ 5.0 s | 3.10 s |

Verdict: **PASS**.

Webots is generated from the same pinned URDF: `webots_env.py` converts it to a
PROTO and writes the world from the same `task.py` geometry, so its shelf and its
robot are the ones the other two engines use. Where Webots is not installed —
GitHub's runners, for instance — `sim2sim` reports the engine as
`unavailable_no_webots_installation` and computes the verdict from the engines
that actually ran. A missing engine never turns a failing comparison into a
passing one.

Two engine-specific details are worth stating plainly, because they are the only
places the three runs differ:

* **Servo implementation.** MuJoCo integrates an explicit PD law; PyBullet and
  Webots use their own implicit joint servos. All three saturate at the same
  URDF effort limits. An explicit PD at these gains is numerically unstable at
  the other engines' fixed steps.
* **Webots servo gain.** Webots' position servo is a velocity-level controller
  whose default gain (P=10) tracks too slowly to hold a 182 kg humanoid — the
  ankle lags, the torso pitches, and Atlas topples after about a second. P=120
  holds the stance at 0.898 m. This was measured, not guessed.

Everything above the servo — the robot, the task geometry, the state machine,
the Jacobian and the gravity feedforward — is shared code.

### 5.1 End-effector speed

An earlier revision of this report quoted 5.76 m/s for MuJoCo. That number was
wrong, and the way it was wrong is worth recording. It read
`data.cvel[hand][:3]`; MuJoCo lays `cvel` out as `[angular; linear]`, so it
reported the hand's angular rate in rad/s as a speed in m/s. All three engines
now measure the same thing — how far the hand actually moved in one control
step — and `test_reported_speed_matches_the_hand_actually_moving` checks the
reported figure against the hand's own displacement, because nothing in the task
fails when this metric is wrong.

| Engine | While inspecting (REACH/VERIFY) | Episode peak | Shelf contacts |
| --- | --- | --- | --- |
| MuJoCo | 0.336 m/s | 1.134 m/s | 0 |
| PyBullet | 0.366 m/s | 3.109 m/s | 0 |
| Webots R2025a | 2.817 m/s | 6.204 m/s | 0 |

Two separate effects, neither of them a physical claim about Atlas:

* **The episode peak is a RETURN artefact.** `RETURN` assigns the stance pose
  straight into the joint targets instead of going through `_servo`, so the
  rate limit that shapes `REACH` does not apply and only the actuator effort
  limits bound the retraction. That peak happens after the last target has been
  verified, so it is reported separately from the speed reached near the shelf.
* **The spread between engines is servo stiffness, not motion planning.** The
  rate limit is `MAX_JOINT_STEP` = 0.01 rad per 2 ms control step — 5 rad/s in
  joint space, which at the ~0.6 m shoulder-to-hand lever is a ceiling of about
  3 m/s at the hand. Webots' stiff position servo tracks the rate-limited target
  closely enough to reach that ceiling; the softer PD servos in MuJoCo and
  PyBullet lag well behind it. The bound is the same in all three; how much of
  it gets used is an engine property.

So the ceiling is set in joint space by the controller, not by a Cartesian speed
limit, and it is not tuned per engine. What is asserted here is only what was
measured: no engine touched the shelf, and the closest the hand came to a target
without contact was 4.9 mm (MuJoCo) and 6.7 mm (PyBullet).

## 6. Payment gate

`demo-e2e-evidence.json` walks the full gate:

| Step | HTTP | Executed | Settled |
| --- | --- | --- | --- |
| No payment | 402 | no | no |
| Wrong amount | 400 | no | no |
| Valid payment, 3/3 targets | 200 | yes | **yes** |
| Replayed receipt | 409 | no | no |

24 payment-safety tests cover the receipt validation, the settlement ledger and
the relay gating. A safely stopped episode returns `completion_reason:
safe_stopped` and `success: false`, so it can never settle.

## 7. Payment-validated action over the tunnel

`demo_e2e.py` proves the payment invariants in-process. `demo_tunnel.py` proves
the transport, with nothing stubbed between the gate and the simulator:

```
payment-validated action request
    -> x402 verification (tunnel side)
    -> Zenoh  robot/tunnel/action
    -> Atlas bridge
    -> MuJoCo inspection episode
    -> Zenoh  robot/tunnel/result
    -> correlation by action_id
    -> settlement, only on success
```

| Request | Verified | Published to Zenoh | Executed | Settled |
| --- | --- | --- | --- | --- |
| No payment | no (402) | **no** | no | no |
| Wrong amount | no (400) | **no** | no | no |
| Malformed `txHash` | no (400) | **no** | no | no |
| Valid receipt | yes | yes | 3/3 targets | **yes** |
| Replayed receipt | no (400) | **no** | no | no |
| Undeclared parameter | yes | yes | rejected by the bridge | no |

### Two gates, and which one each result went through

The bridge applies payment checks in two layers, and this report is deliberate
about which layer proved what:

1. **Protocol checks** — amount, asset, network, expiry, the shape of the
   settlement reference, and replay. Cheap, and they reject the obvious cases
   before anything else happens. The walkthrough's accepted request passes
   these.
2. **Facilitator verification** — the only check that can distinguish a real
   authorization from a well-formed forgery, because only the facilitator
   recovers the signer.

The forgery case is proven against the **live** facilitator: a payload with a
correct amount, asset, network and a perfectly shaped signature is refused with
`invalid_exact_evm_signature`. A dedicated test asserts the uncomfortable half of
that too — the protocol checks *do* accept the same payload, which is exactly why
the facilitator layer exists. Verification also **fails closed**: an unreachable
facilitator is treated as a rejection, never as an approval.

What is **not** claimed: the accepting side of facilitator verification. That
needs a signature from a funded operator wallet, which deliberately does not
exist in this repository. The walkthrough's settled request therefore passed the
protocol layer only, and the evidence file records that explicitly as
`payment_verification: protocol_checks_only`.

### Idempotency

A payment-validated action actuates the robot once. The store is keyed on
`robot_id + skill_id + idempotency_key`, records the parameters and a payment
fingerprint alongside it, and is appended to disk so the guarantee survives a
restart:

| Repeat | Outcome |
| --- | --- |
| Same key, same request | `duplicate` — the recorded outcome is replayed, the robot does not move |
| Same key, different parameters | refused, `IDEMPOTENCY_PARAMS_CONFLICT` |
| Same key, different payment | refused, `IDEMPOTENCY_PAYMENT_CONFLICT` |
| Same key after a restart | still one actuation |
| No key at all | not deduplicated — the caller opted out |

Two properties matter here and both are asserted by the demo itself:

* An unverified payment is **never published to Zenoh**, so the simulator is
  unreachable without a valid receipt.
* Every executed action is answered on `robot/tunnel/result` carrying the
  originating `action_id`, `robot_id`, `skill_id`, `params_hash` and
  `idempotency_key`, which is what lets the tunnel correlate an asynchronous
  result with the request that paid for it.

The transcript is committed at
[`tunnel-e2e-terminal.txt`](../../../../../../docs/evidence/tunnel-e2e-terminal.txt).
Zenoh runs in peer mode, so no external router is needed:

```bash
python -m bridge.boston_dynamics.atlas_bridge.demo_tunnel
```

## 8. On-chain settlement

The gate above decides *whether* an action may settle. This is the receipt that
a settlement actually happened, re-read from a public Base Sepolia RPC by
`settlement_evidence.py` rather than transcribed:

| Field | Value |
| --- | --- |
| Network | Base Sepolia (`eip155:84532`, chain id 84532) |
| Asset | USDC [`0x036CbD53…3dCF7e`](https://sepolia.basescan.org/token/0x036CbD53842c5426634e7929541eC2318f3dCF7e) |
| Settlement tx | [`0x5b04259e…26b6e`](https://sepolia.basescan.org/tx/0x5b04259e0d9cfe319a6ffec3d7f6b9118b70e09ae4a832625bed5ecd48326b6e) |
| Status | success (`0x1`), block 45670338, gas 62147 |
| Event | `Transfer(address,address,uint256)` on the USDC contract |
| Amount | 1.0 USDC (`1000000` raw, 6 decimals) |
| Payer | [`0x520C3Ff276456A217c0dFadABeEb2d7081d6cCd4`](https://sepolia.basescan.org/address/0x520C3Ff276456A217c0dFadABeEb2d7081d6cCd4) |
| Payee | [`0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8`](https://sepolia.basescan.org/address/0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8) |
| Funding | CDP faucet request [`0xb37252fd…fdbbb`](https://sepolia.basescan.org/tx/0xb37252fda0bc30de9ce98bd1b306c131eda11a4b3fabd9ae11d487d8773fdbbb), block 45669921 |

Two deliberate choices about how this is reported:

* **No balances are recorded.** Balances change after the fact; the transaction
  and its `Transfer` event do not. An artefact that claims a balance goes stale
  the moment anything else touches the wallet.
* **No key material anywhere.** The payer is a disposable testnet wallet whose
  key was exposed in an earlier revision of this branch; it is treated as
  compromised and holds nothing of value. Base Sepolia USDC has no monetary
  worth. Settlement is executed by the operator's own wallet at deploy time —
  `payment-policy.yaml` keeps the payee as `<evm_payee_address>` so the profile
  stays portable.

Re-verify it yourself:

```bash
python -m bridge.boston_dynamics.atlas_bridge.settlement_evidence
```

The command exits non-zero if the transaction is missing, reverted, or carries
no USDC `Transfer`.

## 9. Reproducing

```bash
pip install -r bridge/boston_dynamics/atlas_bridge/requirements.txt
python -m bridge.boston_dynamics.atlas_bridge.download_atlas_model
python -m pytest bridge/boston_dynamics/atlas_bridge/tests -q
python -m bridge.boston_dynamics.atlas_bridge.runner
python -m bridge.boston_dynamics.atlas_bridge.pybullet_runner
python -m bridge.boston_dynamics.atlas_bridge.sim2sim
python -m bridge.boston_dynamics.atlas_bridge.demo_e2e
python -m bridge.boston_dynamics.atlas_bridge.demo_tunnel
python -m bridge.boston_dynamics.atlas_bridge.settlement_evidence
```

The runner exits non-zero unless every target was reached and held, the robot is
still standing, and nothing touched the shelf.
