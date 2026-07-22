# Task traceability

This document separates historical physical evidence from the new asynchronous
RoboPay contract. It intentionally masks public-chain identities and omits
workcell coordinates, controller addresses, host names, user names, and device
serial numbers.

## Execution lineage

```mermaid
flowchart LR
    A["Fabric paid request"] --> B["Historical synchronous tunnel"]
    B --> C["Local safety bridge"]
    C --> D["DOBOT RunScript: test"]
    D --> E["Mode 7 and exact project observed"]
    E --> F["Mode 5 and no active project"]
    F --> G["Local bridge HTTP 200"]
    B -. "outer gateway deadline ~30.8 s" .-> H["Historical payer-facing HTTP 502"]

    I["New RoboPay contract"] --> J["Zenoh robot/tunnel/action"]
    J --> K["Durable validation and replay gate"]
    K --> D
    F --> L["Zenoh robot/tunnel/result"]
    L --> M{"result status"}
    M -->|success| N["settlement eligible"]
    M -->|error or timeout| O["must not settle"]
```

The solid historical path through `G` was observed on the physical robot. The
new path `I` through `O` is implemented and unit-tested locally but still
requires a fresh physical end-to-end run with a result-aware Fabric relay.

## Evidence register

| ID | Evidence | Public reference | Privacy treatment | Status |
| --- | --- | --- | --- | --- |
| D-CODE-001 | Team-authored multi-stage Lua logic | `controller-project/src0.lua` | Exact workcell points removed | Public |
| D-ART-001 | Controller export used on 2026-07-15 | SHA-256 `fba9675e7c34bbbd1ef9c0b0710b0eb7b15fbe02362c1aecc6dd8b8e0260d6e8` | Coordinate-bearing ZIP not published | Committed digest |
| D-TX-001 | Base Sepolia payment for the physical run | Private evidence-ledger reference only | Transaction hash, payer, and payee withheld to prevent chain correlation | Historical |
| D-CTRL-001 | Controller/software version capture | Values transcribed in validation report | Serial numbers and screenshot withheld | Historical |
| D-RUN-001 | Privacy-redacted physical two-cycle pick/place video | [`docs/evidence/dobot-cr3v-historical-physical-evidence-redacted.mp4`](evidence/dobot-cr3v-historical-physical-evidence-redacted.mp4); SHA-256 `6c479d7bfcc4143742e144a1984c2e2d718d224b26f0d1b218c9bd79aabdd1a4` (9,996,615 bytes; 30.50 s) | Robot motion only; no audio, terminal, wallet, host, serial, or user data | Included public evidence asset |
| D-ASYNC-001 | New Zenoh action/result physical run | To be captured | Same masking policy | Not yet run |
| D-FAIL-001 | Timeout/error result and no-settlement proof | To be captured from result-aware relay | Wallets and authorization secrets masked | Not yet run |

Historical skill ID `cra_safe_demo` and registry skill ID
`cra_two_cycle_pick_place` are linked by the same fixed project name and
D-ART-001 digest. The registry ID still requires its own fresh paid acceptance
run.

The machine-readable derivative record is
[`docs/evidence/evidence-manifest.yaml`](evidence/evidence-manifest.yaml).

## Requirement mapping

| RoboPay requirement | Implementation or evidence | State |
| --- | --- | --- |
| Custom Tier 3 behavior | D-CODE-001 composes 13 joint/linear moves, two gripper cycles, waits, relative lifts, and home returns | Proven in source and historical robot run |
| Physical robot | CR3V/CC262V hardware and D-RUN-001 | Historical run and privacy-redacted video available |
| Action/result correlation | `actionId` in both Zenoh schemas | Implemented; physical retest pending |
| Envelope integrity | `paramsHash`, expiry, payment-policy checks | Unit-testable |
| Durable replay protection | SQLite idempotency/fingerprint store | Unit-testable |
| Failure semantics | Structured error, `Stop`, timeout, `settlementEligible: false` | Bridge unit-testable |
| No settlement on failure | Relay must consume result before settlement | Contract specified; result-aware relay test pending |
| Privacy | Environment variables, masked chain identity, sanitized taught points | Applied to this profile |
