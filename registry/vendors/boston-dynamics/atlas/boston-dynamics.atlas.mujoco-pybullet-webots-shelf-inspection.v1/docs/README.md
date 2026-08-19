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
| No payment | 402 | no | no |
| Wrong amount / asset / network | 400 | no | no |
| Valid payment, 3/3 targets | 200 | yes | **yes** |
| Valid payment, failed or stopped | 200 | yes | no |
| Replayed receipt | 409 | no | no |

Reviewer-facing expectations, each mapped to the test that enforces it, are in
[`../tests/skill-contract.test.yaml`](../tests/skill-contract.test.yaml).

## On-chain settlement

A real settlement of this skill on Base Sepolia:
[`0x5b04259e…26b6e`](https://sepolia.basescan.org/tx/0x5b04259e0d9cfe319a6ffec3d7f6b9118b70e09ae4a832625bed5ecd48326b6e)
— 1.0 USDC transferred, block 45670338, status success. Re-read from chain by
`settlement_evidence.py` into
[`docs/evidence/onchain-settlement.json`](../../../../../../docs/evidence/onchain-settlement.json).
Testnet only; no key material is stored in this repository.
