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
| Paid action | A live-facilitator-verified payment executed the skill and settled 0.001 USDC, bound to the `action_id` | `real-paid-run.json` |
| One recording of all of it | The 402, the signed authorization, the 202, the episode, the correlated result and the settlement — one pass, one action | `atlas-paid-action.gif` |
| Full relay path | Discovery, priced 402, paid action, execution and settlement through the **hosted Fabric relay** with nothing stood in for | `fabric-relay-e2e.json` |
| Failure is not charged | A refused action returns an error, settles nothing, and the token contract confirms the authorization was never spent | `fabric-relay-failure.json` |
| On-chain settlement | The settlement transaction, re-read from a public RPC | `real-paid-run.json`, `onchain-settlement.json` |

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

| Step | HTTP | Executed | Settlement |
| --- | --- | --- | --- |
| No payment | 402 | no | none |
| Wrong amount | 400 | no | none |
| Protocol-valid payment, 3/3 targets | 200 | yes | **eligible, not on chain** |
| Replayed receipt | 409 | no | none |

That `409` is this in-process relay's answer, and only its own: it replies
synchronously, so it can refuse with a code. The transport demo answers `400`,
and the hosted Go tunnel has already answered `202` by the time the bridge
recognises the duplicate, so it reports `DUPLICATE_ACTION` on the status
endpoint instead. None of the three actuates the robot a second time.

This demo runs in-process and holds no wallet, so the successful step is
recorded as `SETTLEMENT_ELIGIBLE` with `settlement_tx_hash: null`. That
distinction is enforced by the ledger rather than by discipline: `SETTLED`
requires a settlement transaction hash **and** the block containing it, and
`test_settled_requires_a_real_transaction` fails if a receipt alone can earn it.
The receipt a caller presents is an input that authorises the run; it is not a
transfer, and an artifact that reports one as the other claims money moved when
it did not.

Where value really moves is section 8.

37 payment-safety tests cover the receipt validation, the settlement ledger and
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
| Valid receipt † | protocol checks only | yes | 3/3 targets | eligible, **not on chain** |
| Replayed receipt | no (400) | **no** | no | no |
| Undeclared parameter | protocol checks only | yes | rejected by the bridge | no |

† A synthetic receipt. This walkthrough proves the **transport**, so its accepted
row is protocol-level and settles nothing — the artifact records
`payment_verification: protocol_checks_only` and `settlement: eligible_not_on_chain`.
Sections 8 and 9 are where a real payment is verified and real value moves.

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

**Which layer proved what, in one place.** This walkthrough's accepted request
passed the protocol layer only; it holds no wallet, so it settles nothing. The
accepting side of facilitator verification is **not** proven here — it is proven
in section 8.1 (`real-paid-run.json`, `isValid: true` from the live facilitator,
0.001 USDC settled) and again in section 9 through the hosted relay. No claim in
this section depends on a funded wallet, and none of it should be read as the
profile's evidence for a real payment.

The single source of truth for what has been paid for:

| Path | Payment verification | Settlement |
| --- | --- | --- |
| `demo-e2e-evidence.json` (in-process) | protocol checks only | eligible, none |
| `tunnel-e2e-evidence.json` (Zenoh transport) | protocol checks only, plus a **live** facilitator rejection | eligible, none |
| `go-tunnel-e2e-evidence.json` (real tunnel) | live facilitator, refusals only | none |
| `real-paid-run.json` (§8.1) | **live facilitator accepted** | **0.001 USDC on chain**, after execution |
| `fabric-relay-e2e.json` (§9) | **live facilitator accepted, via the hosted relay** | **0.001 USDC on chain**, after execution |
| `fabric-relay-failure.json` (§9.1) | live facilitator accepted | **none** — execution failed, and the token confirms the authorization was never spent |

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

The gate above decides *whether* an action may settle. This section is the
receipt that one actually did — for a specific action, not in general.

### 8.1 The paid action

`real-paid-run.json` records one action carried the whole way: an EIP-3009
authorization signed by a funded wallet, accepted by the **live** x402
facilitator, executed on the robot, and settled only after the episode reported
every target reached.

| Field | Value |
| --- | --- |
| Action | `act-paid-de66513f791b` |
| Facilitator verdict | `isValid: true` — live, at `https://x402.org/facilitator` |
| Robot | success, 3/3 targets, 0 shelf contacts, correlated by `action_id` |
| Settlement tx | [`0x2b3b71d0…c0f39`](https://sepolia.basescan.org/tx/0x2b3b71d0ce18554a4927e1145a704359bad35c209f632dc414926b995aac0f39) |
| Status | success (`0x1`), block 45706216, gas 85696 |
| Amount | 0.001 USDC (`1000` raw) — the price the profile declares |
| Payer | [`0xa0597a74…Fc2Dc`](https://sepolia.basescan.org/address/0xa0597a74f3C3F33797007495bc3Dc676F10Fc2Dc) |
| Payee | [`0x7b916325…C3e8`](https://sepolia.basescan.org/address/0x7b9163254A21b249a0D3E34300fC81BB0A43C3e8) |
| Submitted by | [`0xd407e409…f1bf`](https://sepolia.basescan.org/address/0xd407e409E34E0b9afb99EcCeb609bDbcD5e7f1bf) |

Three things about this are worth more than the transaction itself.

**The settlement is bound to the action, cryptographically.** EIP-3009 lets the
signer pick the 32-byte authorization nonce, so this run sets it to
`keccak256(action_id)`. The token emits that nonce in its `AuthorizationUsed`
event, so the binding is on chain and anyone can check it:

```
keccak256("act-paid-de66513f791b")
  = 0xaa6cf89a24e6ee6471a1dde2a1e9eee101d60213f9231132ea717affd03b47de
AuthorizationUsed nonce in block 45706216
  = 0xaa6cf89a24e6ee6471a1dde2a1e9eee101d60213f9231132ea717affd03b47de
```

Without this, a receipt and an execution are two facts sitting next to each
other; with it, this transfer is provably the one that paid for this action.

**The facilitator submitted the transaction, not us.** The sender is the
facilitator's own address, the one its `/supported` endpoint advertises. That is
independent evidence the payment went through the live facilitator rather than
being self-submitted — and it is why the payer holds no ETH: under EIP-3009 the
payer only signs, and the facilitator pays the gas.

**Settlement followed execution.** `/verify` ran first, the robot ran second,
and `/settle` was called only because the episode reached every target. A failed
episode leaves the authorization signed and unspent, which is the behaviour the
payment policy claims.

Re-verify the whole chain from the transaction hash alone — no trust in this
document required:

```bash
python -m bridge.boston_dynamics.atlas_bridge.settlement_evidence
```

### 8.2 What CI re-verifies on every push

`onchain-settlement.json` is regenerated by `settlement_evidence.py`, which
reads the settlement out of `real-paid-run.json` rather than from a hard-coded
hash, so the check follows the evidence instead of drifting from it. It exits
non-zero unless the transaction succeeded **and** its `AuthorizationUsed` nonce
equals `keccak256(action_id)` — a settlement of the right size that is not bound
to the action proves the asset moved, not that this action was the reason.

An earlier revision of this file verified a 1.0 USDC transfer that predated the
paid run and was bound to no action at all, while the profile's real settlement
was 0.001 USDC. The step was green and was checking the wrong transaction.

The wallet that made it was funded by a CDP faucet request,
[`0xb37252fd…fdbbb`](https://sepolia.basescan.org/tx/0xb37252fda0bc30de9ce98bd1b306c131eda11a4b3fabd9ae11d487d8773fdbbb),
block 45669921. No settlement other than the ones in 8.1 and 9 is evidence for
this skill; an earlier 1.0 USDC transfer between two test wallets exists on
chain but is bound to no action and is not cited here.

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

## 9. The whole path, with nothing stood in for

Two substitutions ran through the sections above, and this one removes both.
`demo_go_tunnel.py` drives the real tunnel but stands in for the hosted Fabric
backend with a local WebSocket proxy; `real_paid_run.py` settles a real payment
but reaches the robot over Zenoh directly. `fabric-relay-e2e.json` records a run
where every component is the real one:

```
client
  -> Fabric relay   https://api.fabric.foundation/api/core   (hosted)
  -> Go tunnel      this repository's binary, dialled out over WSS
  -> x402 middleware -> live facilitator
  -> Zenoh          robot/tunnel/action
  -> Atlas bridge   -> MuJoCo, three inspection targets
  -> Zenoh          robot/tunnel/result
  -> Fabric relay   terminal status, correlated by action_id
  -> settlement     0.001 USDC on Base Sepolia
```

| Step | Result |
| --- | --- |
| Action | `atlas-inspect-1787197727` |
| Robot discovery | `GET /robots/{id}/skills` → 200, robot connected |
| Skill discovery | `inspect_shelf`, `stop` |
| Price discovery | 0.001 USDC — read from the response, not assumed |
| Unpaid action | **402** from the relay, with payment requirements |
| Quoted amount | `1000` raw, matching the discovered price |
| Paid action | **202 accepted** immediately, before the robot finished |
| Execution | 3/3 targets |
| Terminal status | `succeeded`, correlated by `action_id` |
| Settlement | [`0xfd9eda75…1e6940`](https://sepolia.basescan.org/tx/0xfd9eda75ddc6c6f979eb2571e6e85ef3a6f50d670f3f8ad252107723e21e6940), block 45714728 |
| Binding | on-chain nonce = `keccak256("atlas-inspect-1787197727")` |
| Token's own record | `authorizationState(...) = true` — the authorization was spent |

**The price is discovered, not assumed.** The payment is built from the amount
the relay returns in its 402, and the run asserts that amount equals the price
the catalogue advertises. A profile whose published price drifted from what its
tunnel charges would fail this check rather than pass it quietly.

**Discovery answers from the profile's own catalogue.** `GET /skills` reads
`skill-catalog.json` — the file the registry publishes — so there is no second
copy of the price to drift.

**What the tunnel gained to make this possible.** Three read-only endpoints —
`GET /robot`, `GET /skills`, `GET /action/:action_id/status`. The status
endpoint is not synthesised: the tunnel subscribes to the same
`robot/tunnel/result` topic the simulator publishes on and stores what arrives,
keyed by `action_id`; an unanswered action reads as `pending` and a failed one as
`failed`.

### 9.1 A failed action is not paid for

`fabric-relay-failure.json` sends a paid action whose `maxDurationSec` is below
the bound the catalogue declares, through the same relay, with the same wallet.

| | |
| --- | --- |
| Action | `atlas-inspect-1787197752` |
| Execution | refused — `INVALID_DURATION` |
| Tunnel's answer | **HTTP 202**, immediately — acceptance is about the request, not the outcome |
| Status endpoint | `failed`, carrying that error code, correlated by `action_id` |
| Settlement | **none** — no transaction exists |
| Token's own record | `authorizationState(...) = false` |

The last row is the one that matters. "We recorded no transaction hash" is an
absence of evidence; it proves nothing about whether the payer was charged.
EIP-3009 tokens keep their own map of spent authorization nonces, so the
question can be put to the contract instead — and because the nonce is
`keccak256(action_id)`, anyone can recompute it from the action id alone and ask
USDC directly whether this action was ever paid for. The answer is no.

**How the guarantee is enforced.** The tunnel contract answers `202` the moment
an action is accepted, so the HTTP response cannot carry the outcome and must
not carry the payment decision either. The stock x402 gin middleware settles as
soon as a protected route answers anything under 400, which would charge the
payer on that `202` before the robot had run. The tunnel therefore replaces it
with a gate that keeps the `402`/verify half exactly as it was — an unpaid
request still gets `402` with the advertised requirements, and a payment the
live facilitator rejects still never reaches the robot — but hands a settlement
callback to the action handler instead of settling. A background watcher invokes
that callback only when the correlated result reports success; a failure or a
silent robot leaves the authorization signed and unspent, and both are readable
from the status endpoint. Eight tests in
`tunnel/internal/handlers/handlers_test.go` hold that contract without needing a
wallet or a chain, including that a refused request is never published to Zenoh
at all.

An earlier revision of this profile settled on *acceptance* instead, before the
robot ran, and a refused action was still charged. That was measured, not
suspected, and it is what prompted the change.

**On why the failure is a refused parameter rather than a timeout.** The
catalogue declares `maxDurationSec` minimum 5, and at 5 seconds the episode
completes all three targets — measured over three runs, not assumed. There is
therefore no in-bounds duration that produces an execution timeout, which is a
property of a well-chosen bound rather than a gap. Execution-level failures that
must not settle — falls, shelf contact, safe stop — are covered in section 6 and
by `tests/test_x402_payment_safety.py`.

### 9.2 What this profile does not prove

**Robot identity, the outbound client, and the payee.** Three things get
conflated here, so they are separated.

*The outbound client.* The success criteria describe the bridge connecting out
to the relay "using `robotsdk`". There is no package by that name in this
repository; the robot-side outbound client it ships is `tunnel/`, and the relay
connection itself is `tunnel/internal/client.go`, which dials the WSS endpoint
with `gorilla/websocket`. The same module also carries
`github.com/unibaseio/aip-go-sdk`, used by `cmd/main.go` and `internal/aipagent`
for the optional authenticated AIP path — not for that transport. This profile
uses the tunnel as it is, adding three read-only endpoints and changing no
transport behaviour. The merged Tier-1 profiles use the same component.

*The authentication handshake.* It exists in that tunnel and this profile does
not bypass it. With `AIP_ENABLED=true`, `cmd/main.go` runs
`aipauth.EnsureAuth`, which returns a bearer token and a wallet address, and
`internal/aipagent` then registers the agent with `Handle` set to the robot id
and `UserID` set to that wallet — identity bound to wallet, by the shared
tunnel, through the SDK. The demos recorded here run with it disabled, because
`EnsureAuth` drives an interactive browser authorization flow that a
reproducible, unattended demo cannot perform. So: implemented in the component
this profile uses, not exercised in these recordings, and not reimplemented
here.

*The payee, which this profile can and does hold.* The identity the tunnel
answers for and the address it is paid to come from one configuration and are
advertised together, so a caller can see which wallet the robot it is talking to
gets paid at before paying anything: `GET /robot` returns `robot_id` and
`pay_to` from that config, the `402` quotes the same `payTo`, and the x402
middleware refuses a payment whose `payTo` differs — it matches on scheme,
network, amount, asset **and** payee. The settlements in 8.1 and 9 landed at
exactly that address. `TestTheAdvertisedPayeeIsTheConfiguredOne` and
`TestAnUnconfiguredPayeeIsNotAdvertisedAsAnAddress` hold that half.

What this profile therefore does **not** claim: that the recorded runs exercised
the authenticated registration, or that a robot profile could make an identity
unforgeable on its own. It cannot; that belongs to the tunnel and the gateway.

What *is* bound cryptographically is the settlement to the action: the
authorization nonce is `keccak256(action_id)` and the token records it on chain.
That is a different property and it is proven in 8.1 and 9.

## 10. Reproducing

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
