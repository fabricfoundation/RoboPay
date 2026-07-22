# Validation report — agibot.x2.aimdk-robopay.v1

Submission scope: physical robot, Tier 2 (vendor-provided built-in skill)

Brand: Fabric Foundation × AgiBot

Robot/control stack: physical AgiBot X2, AimDK_X2 1.0.0, ROS 2 Humble,
`/aimdk_5Fmsgs/srv/SetMcPresetMotion`

Zenoh topics: `robot/tunnel/action` and `robot/tunnel/result`

## Claim boundary

Four evidence groups are kept separate:

1. **Historical real x402 payment and settlement:** the privacy-redacted payment
   terminal shows HTTP 200, `settlement.success=true`, transaction
   `0x35fa38...605a0a`, Base Sepolia, and "action paid for and payload delivered"
   at `2026-07-13T12:55:29+08:00`.
2. **Time-correlated robot execution:** the privacy-redacted bridge terminal
   records AimDK `RUNNING`, `task_id=8`, at epoch `1783918529.509...`
   (`2026-07-13T12:55:29.509+08:00`), within the same wall-clock second as the
   payment record. The redacted video shows the corresponding physical X2
   right-hand wave.
3. **Independent historical paid success:** transaction `0x4f46…1798e` is a
   separate successful Base Sepolia paid request. It is not claimed to appear
   in, or correlate with, the `task_id=8` video sequence.
4. **Current strict profile:** adds full envelope validation, persistent replay
   protection, structured asynchronous results, and settlement eligibility
   semantics. It has automated offline coverage but has not yet been deployed
   for a new physical full-flow run.

Accordingly, **historical real x402 payment validation and settlement
completed**; it is inaccurate to describe this submission as never having
completed a real payment. The historical flow settled before terminal robot
completion, however, and therefore does not prove the current
success-after-result or no-settle-on-failure rules. The historical artifacts
are supporting evidence, not substitutes for that required rerun.

## Historical physical validation

- [x] Physical AgiBot X2 identified and prepared in Stable Standing Mode.
- [x] `x2_right_wave` mapped to AimDK area `2`, motion `1002`.
- [x] Unpaid request returned HTTP 402.
- [x] Paid request returned HTTP 200/accepted on Base Sepolia.
- [x] Verified payment amount was 0.002 USDC.
- [x] Historical x402 settlement returned `success=true` for
      `0x35fa38...605a0a`.
- [x] Payment terminal confirmed "action paid for and payload delivered."
- [x] Zenoh action reached the robot-side adapter.
- [x] AimDK accepted the action and returned `task_id=8` with `RUNNING`.
- [x] Physical right-hand wave was visibly observed and recorded.
- [ ] Historical logs contain an explicit AimDK terminal `SUCCESS` result.
- [ ] Historical flow proves no-settle-on-failure.

Public identifiers are privacy-masked:

| Field | Redacted value |
| --- | --- |
| Video-correlated transaction | `0x35fa38...605a0a` |
| Independent successful transaction | `0x4f46…1798e` |
| Historical payer | `0x8c0c…912F` |
| Historical payee | `0x3F5a…3987b` |
| Robot identity | `agibot-x2-demo-***` |

Full receipt material is retained outside Git for controlled reviewer
verification. No private key, payment signature, hostname, username, internal
IP, or robot serial number is included in this package.

## Historical evidence manifest

| Evidence ID | Artifact | SHA-256 | Size | What it proves | Publication status |
| --- | --- | --- | ---: | --- | --- |
| `AGX2-HISTORICAL-PAYMENT-TERMINAL-01` | [`docs/evidence/terminal/agibot-x2-historical-payment-terminal-redacted.png`](evidence/terminal/agibot-x2-historical-payment-terminal-redacted.png) | `0322cb26d6882b911f481a0c48ab123eac9d1d2cf3e9c666d207be4e1f7f3557` | 364,536 bytes | Historical HTTP 200, `settlement.success=true`, masked `0x35fa38...605a0a`, Base Sepolia, and payload-delivered confirmation at `2026-07-13T12:55:29+08:00` | Included; deterministic crop plus opaque masks |
| `AGX2-HISTORICAL-BRIDGE-TASK8-01` | [`docs/evidence/terminal/agibot-x2-historical-bridge-task8-redacted.png`](evidence/terminal/agibot-x2-historical-bridge-task8-redacted.png) | `46adddfad2d19e3799645a95bd969fd0d458fc03f1c6e106a222639d6b2613e1` | 357,253 bytes | Bridge admission for AimDK `task_id=8` at epoch `1783918529.509...`, the same wall-clock second as the payment record | Included; deterministic crop with local paths excluded |
| `AGX2-HISTORICAL-PHYSICAL-01` | [`docs/evidence/agibot-x2-historical-physical-evidence-redacted.mp4`](evidence/agibot-x2-historical-physical-evidence-redacted.mp4) | `242620d1982bbd1a80778319f6433f49e9ca434e39b83d96ff0268d6856fb70f` | 2,194,472 bytes | Privacy-redacted physical wave associated with task 8 and `0x35fa38...605a0a` | Included; video-only, no audio or source metadata |
| `AGX2-PAYMENT-TERMINAL-SOURCE-01` | `proof-payment.png` | `8543554abfd084aef133a99804187998bcfb8cd5f1be10993b2cb2ab116c185e` | n/a | Private source for the payment-terminal derivative | Kept outside Git; contains full payer/transaction and a local path |
| `AGX2-BRIDGE-TERMINAL-SOURCE-01` | `proof-bridge.png` | `84189613b21904028dcb17a4c443b7ce13ec9881746fea9d09f26dfa3e6c48db` | n/a | Private source for the bridge-task derivative | Kept outside Git; contains local user/host paths |
| `AGX2-LEGACY-SOURCE-01` | `FABRIC-AgiBot-X2-English-Narrated-Demo.mp4` | `1221d214d22dddea1f82f8c0e89fcea546208628eee8605213ad5a580fbc44eb` | 10,442,891 bytes | Source evidence used to derive the public physical-action clip | Kept outside Git because it contains private terminal and user data |
| `AGX2-LEGACY-TX-01` | Base Sepolia receipt `0x35fa38…605a0a` | masked | n/a | Payment receipt correlated with the task 8 video evidence | Full value withheld from public tree |
| `AGX2-LEGACY-TX-02` | Base Sepolia receipt `0x4f46…1798e` | masked | n/a | Independent historical paid success only; no task 8 video correlation claimed | Full value withheld from public tree |

The two terminal derivatives use deterministic cropping and opaque masking, not
generative reconstruction. Payer addresses, complete transaction hashes, and
local paths remain private; public overlays preserve only truncated references.
The raw images are not uploaded, but their SHA-256 values make the derivation
auditable in a controlled review. The machine-readable privacy and traceability
record is in [`docs/evidence/evidence-manifest.yaml`](evidence/evidence-manifest.yaml).
The task/video evidence is correlated only with `0x35fa38...605a0a`; the report
does not infer any video relationship for `0x4f46…1798e`.

## Current automated validation

Run from this profile directory:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Local result on 2026-07-22: **26/26 tests passed** with Python 3.12.13.

- [x] Canonical `paramsHash` matches the committed example.
- [x] Valid explicit success produces a correlated structured result.
- [x] `RUNNING` remains pending and is not settlement-eligible.
- [x] Dry-run results cannot authorize settlement.
- [x] Duplicate idempotency/action/payment-authorization IDs do not execute twice.
- [x] Replay protection persists across bridge restart.
- [x] Wrong robot, unknown skill, invalid params, and tampered hash do not actuate.
- [x] Unverified, mismatched, settled, or expired payment evidence does not actuate.
- [x] Authorization TTL above 300 seconds is rejected before actuation.
- [x] Issuance beyond the 30-second future-clock allowance is rejected.
- [x] Unsafe TTL/skew configuration above the hard caps is rejected.
- [x] Vendor error produces `status=error` and `settlementEligible=false`.
- [x] Audit output omits payee and payment authorization identifiers.
- [x] AimDK state normalization handles both direct integers and ROS `.value` wrappers.

These tests use a fake executor. They validate the contract and fail-closed
routing behavior, not physical motion or on-chain settlement.

## Required strict physical rerun

- [ ] Robot and skill discovery show profile, price, and physical scope.
- [ ] Unpaid request returns 402 and produces no Zenoh action.
- [ ] Paid request returns immediately as accepted/pending with `actionId`.
- [ ] Action envelope preserves every required correlation/payment field.
- [ ] Authorization is fresh, no longer than 300 seconds, and within clock tolerance.
- [ ] Current adapter receives the action and invokes AimDK exactly once.
- [ ] Explicit terminal completion is correlated on `robot/tunnel/result`.
- [ ] Physical wave is visible in a newly redacted Fabric-branded video.
- [ ] Duplicate after adapter restart causes no second motion.
- [ ] Intentional robot-unavailable/error case proves no settlement.
- [ ] Success settles only after the terminal success result.
- [ ] Robot `robotsdk` identity handshake is bound to the configured payee.
- [ ] Final evidence manifest records hashes, capture time, and redaction review.

Follow [field-validation-runbook.md](field-validation-runbook.md) for the safe
capture sequence.

## Known limitations

- AimDK documents `RUNNING` as an accepted/in-progress response but does not
  document a task-ID completion query for this preset service. The adapter
  therefore does not upgrade `RUNNING` to success using a timer or video alone.
- The shared repository tunnel must consume `robot/tunnel/result` and gate
  settlement before the new end-to-end test can pass.
- Physical e-stop remains an operator responsibility; no paid remote stop is
  exposed.
- Public proof is intentionally privacy-masked. A reviewer needing an
  unredacted receipt must use a controlled verification channel.

## Acceptance decision

The project is correctly classified as **Tier 2**. Historical real x402 payment
validation and settlement completed, and the historical real-robot task is
traceable through the privacy-redacted evidence above. The code package is ready
for review and integration testing, but the newer result-gated RoboPay success
criteria should remain unchecked until the strict physical rerun is completed.
