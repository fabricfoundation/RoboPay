# Atlas — paid shelf inspection (Tier 1)

Payment-gated, policy-driven shelf inspection on a free-standing
**Boston Dynamics Atlas v4**, validated in MuJoCo, PyBullet and Webots R2025a
from one pinned robot description.

![Atlas shelf inspection](../../../../../../docs/evidence/atlas-shelf-inspection.gif)

| | |
| --- | --- |
| Skill | `inspect_shelf` |
| Policy | `atlas-shelf-inspection-dls-v1` |
| Robot | Atlas v4, fetched from `openai/roboschool@d32bcb2` (MIT), nothing vendored |
| Base | Free-standing — no weld, no external support |
| Result | 3/3 targets on all three engines, 9.0–12.2 mm mean error, 0 falls, 0 collisions |
| Bridge | [`bridge/boston_dynamics/atlas_bridge`](../../../../../../bridge/boston_dynamics/atlas_bridge) |

The full measurement write-up, including the reach envelope the shelf geometry
is derived from, is in [`validation-report.md`](validation-report.md). Raw
artefacts and their checksums are listed in
[`evidence/evidence-manifest.yaml`](evidence/evidence-manifest.yaml).

## Sequence

```
STAND ──▶ REACH(t) ──▶ VERIFY(t) ──▶ … ──▶ RETURN ──▶ DONE
              ▲            │
              └────────────┘   hold broken → re-converge
```

Each control tick re-solves a damped least-squares resolved-rate step from the
measured end-effector pose. There is no recorded trajectory in the bridge.

## Payment invariant

| Case | HTTP | Executed | Settled |
| --- | --- | --- | --- |
| No payment | 402 | no | none |
| Wrong amount / asset / network | 402 | no | none |
| Missing any identity field | 400 | no | none — nothing published to Zenoh |
| Valid payment | **202** accepted | asynchronously | **after** the result, only on success |
| … and the episode reached 3/3 | — | yes | **0.001 USDC settled** |
| … and the episode failed, timed out or was stopped | — | reported `failed` | none |
| Replayed receipt or a reused idempotency key | 202 | no second actuation | none |

Acceptance is about the request, not the outcome: `202` says the action was taken
in, and the terminal result is read from `GET /action/{action_id}/status`.
Settlement follows that result and never precedes it. A replay is refused by the
bridge and surfaces in the correlated result rather than as an HTTP code, because
the tunnel has already answered by then.

Reviewer-facing expectations, each mapped to the test that enforces it, are in
[`../tests/skill-contract.test.yaml`](../tests/skill-contract.test.yaml).

## On-chain settlement

A real settlement of this skill on Base Sepolia:
[`0x2b3b71d0…c0f39`](https://sepolia.basescan.org/tx/0x2b3b71d0ce18554a4927e1145a704359bad35c209f632dc414926b995aac0f39)
— **0.001 USDC**, the price this catalogue publishes, block 45706216, status
success. It is bound to the action it paid for: the authorization nonce is
`keccak256("act-paid-de66513f791b")`, which the USDC contract records in its
`AuthorizationUsed` event, so a reviewer can recompute it from the action id
alone. `settlement_evidence.py` reads the transaction out of
`real-paid-run.json` and re-checks it against a public RPC into
[`docs/evidence/onchain-settlement.json`](../../../../../../docs/evidence/onchain-settlement.json),
failing if the amount, asset, payer, payee or binding is not what this profile
declares. Testnet only; no key material is stored in this repository.
