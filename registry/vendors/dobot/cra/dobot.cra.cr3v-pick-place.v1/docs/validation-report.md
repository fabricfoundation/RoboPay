# Validation report

## Submission

- Brand: Fabric Foundation × DOBOT
- Scope: physical robot
- Tier: 3, custom multi-stage manipulation
- Robot: DOBOT CR3V with CC262V controller
- Controller firmware: `4.6.5.3-stable-450b2cf77-2026030101605`
- DobotStudio Pro: `4.6.2.5`
- Safety controller: `1.5.2.9`
- Payment network: Base Sepolia (`eip155:84532`)
- Skill: `cra_two_cycle_pick_place`

Controller address, workstation identity, serial numbers, payer address, payee
address, and exact taught points are intentionally excluded.

## What was physically validated on 2026-07-15

- [x] The historical x402 flow returned a real HTTP 402 payment challenge.
- [x] The paid request was validated and settlement completed on Base Sepolia
  (public transaction fingerprint `0xeeb0878b...38d459d3`).
- [x] The historical tunnel published the settled robot action and recorded
  `Robot-Dispatch-Status: completed` with HTTP 200.
- [x] The local bridge accepted only the fixed allow-listed project `test`.
- [x] `RunScript("test")` was issued once without automatic retry.
- [x] Controller mode 7 and the exact active project name were observed.
- [x] The robot visibly completed the custom two-cycle pick/place motion.
- [x] The controller returned to enabled-idle mode 5 with no active project.
- [x] The local bridge returned HTTP 200 after completion.
- [x] The executed controller export matched SHA-256
  `fba9675e7c34bbbd1ef9c0b0710b0eb7b15fbe02362c1aecc6dd8b8e0260d6e8`.

The historical paid payload used skill ID `cra_safe_demo`. This registry uses
the clearer public ID `cra_two_cycle_pick_place`; both map to the same fixed
controller project and the artifact digest above. A new acceptance run must use
the registry ID, so this rename is not presented as already validated.

The full transaction hash, payer, payee, x402 payloads, response UUIDs, and raw
screenshots are retained in the private evidence ledger as D-TX-001. The
privacy-reviewed public terminal derivative discloses only the transaction
fingerprint `0xeeb0878b...38d459d3` and the minimum completion fields needed to
cross-reference that ledger. The manifest records the SHA-256 of each
privately retained source alongside its public derivative for chain-of-custody
identification.

The historical claim established by these records is explicit: **historical
real x402 payment validation and settlement completed**. That claim is narrower
than the new result-aware contract and does not establish its required ordering.

## Historical synchronous limitation

The old public payer request returned HTTP 502 after approximately 30.8
seconds, even though real payment validation and settlement, tunnel delivery,
controller execution, physical motion, and the local bridge's eventual HTTP
200 all succeeded. The old tunnel waited synchronously for the full robot
motion and exceeded an outer gateway deadline. The caller-facing 502 does not
erase the completed historical payment or robot execution, but this run is not
represented as a compliant asynchronous RoboPay success.

The historical tunnel also settled payment before dispatching the controller
project. It therefore proves a paid physical trigger, but it does **not** prove
the current no-settle-on-failure requirement. The new bridge rejects an envelope
marked already settled; the result-aware relay must settle only after success.

The profile replaces that robot-side HTTP wait with Zenoh action/result topics,
an immediate-acceptance contract, and `actionId` correlation. A fresh physical
run is still required to prove the complete new relay behavior.

## New-contract validation matrix

- [ ] Skill catalog returns the published skill and `0.002 USDC` price.
- [ ] Unpaid request returns 402 and publishes no Zenoh action.
- [ ] Paid request returns immediate 202 accepted/pending.
- [ ] Zenoh action preserves `actionId`, `robotId`, `skillId`,
  `idempotencyKey`, `paramsHash`, and `payment`.
- [ ] Physical robot completes from the new Zenoh action path.
- [ ] Correlated Zenoh success result reaches the Fabric status endpoint.
- [ ] Invalid, expired, and replayed requests publish no action and never
  actuate the robot.
- [ ] Deliberate controller failure/timeout produces an error result.
- [ ] Relay logs prove error/timeout does not settle payment.
- [ ] Successful result produces a settlement receipt.
- [ ] Publication video pairs the robot motion with privacy-redacted logs.

Unchecked items are not claims of completion. They are the exact physical and
relay acceptance run required before the final PR is marked ready for review.

## Local automated checks

The profile tests cover:

- required YAML/JSON structure and public privacy scans;
- canonical `paramsHash`, expiry, payment-policy, and wrong-robot rejection;
- durable duplicate suppression and idempotency conflict;
- structured success/error results and settlement eligibility;
- DOBOT project state-machine completion and `Stop` on failure.

Record the final command output here immediately before the PR:

```text
uv run --with pyyaml python -m unittest discover -s tests -p "test_*.py" -v
# 2026-07-22: 23 tests passed

uvx ruff check bridge/dobot_cra_zenoh_bridge.py tests/test_bridge.py tests/test_profile.py
# 2026-07-22: all checks passed

uvx mypy --ignore-missing-imports bridge/dobot_cra_zenoh_bridge.py
# 2026-07-22: success, no issues found

uv run --with eclipse-zenoh python -c "import zenoh; print(type(zenoh.Config()).__name__)"
# 2026-07-22: Config (dependency/API smoke test)
```

The repository's pre-existing Go tunnel baseline was also attempted with
`go test ./...`. It did not complete because the configured module proxy could
not fetch `aip-go-sdk`, and the baseline `zenoh-go` calls do not compile against
the version in `go.mod`. This profile does not modify those Go files; the issue
must be resolved or superseded by the result-aware relay before final PR CI.

## Evidence and privacy

See `task-traceability.md`. The public controller logic and sanitized point
template prove the custom behavior while preventing unsafe reuse of a site's
coordinates. D-HTTP402-001, D-SETTLE-001, and D-BRIDGE-001 are deterministic
crops of the original terminal captures with opaque masks over x402 payloads,
response identifiers, full payer/transaction values, workstation paths, and
the controller's private address. D-RUN-001 has no audio or source metadata,
and its bystander/laptop area is opaque-masked. Raw terminal captures, logs,
source video, wallets, controller addresses, serials, and exact points remain
outside Git.

The terminal images prove the historical 402/payment/settlement/bridge facts
visible in them. They do not prove that settlement waited for a terminal
`robot/tunnel/result`, nor do they prove failure-without-settlement.

## Known limitations

- This profile cannot by itself make the upstream relay defer settlement; the
  relay must treat `robot/tunnel/result` as the settlement gate.
- A crash after controller acceptance but before the terminal result leaves a
  durable unresolved idempotency record. The bridge fails closed and requires
  operator reconciliation; it never automatically repeats an ambiguous move.
- `Stop()` is a software mitigation, not an emergency-stop substitute.
- The public point template is intentionally non-deployable.
- The local artifact hash is approval evidence, not controller-side byte-level
  attestation; deployment remains under operator change control.
