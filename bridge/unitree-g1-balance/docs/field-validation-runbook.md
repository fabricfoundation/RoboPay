# Field Validation Runbook — unitree-g1 balance-recover (RoboPay Tier 1)

Step-by-step guide for the maintainer to reproduce every acceptance claim in
this PR on a clean checkout. All commands run from the repository root unless
noted. No secrets are required: payment keys are read from environment
variables and never committed.

## 0. Prerequisites

```bash
# ubuntu-22.04, Python 3.11
pip install -r bridge/unitree-g1-balance/requirements.txt
pip install "x402>=0.2.0" eth-account web3 httpx
```

## 1. Unit tests (Criterion #1/#3/#4/#5/#6)

```bash
cd bridge/unitree-g1-balance
pytest -q
```

Expected: full suite passes; on the reference Linux platform the PyBullet
dynamic sim-to-sim layer runs for real. On Windows a few tests skip
(`pybullet`/`zenoh` have no Windows wheels); their call paths are still covered
by `tests/bullet_stub.py`.

## 2. Real Go Tunnel payment gate (Criterion #1/#4)

```bash
make build                          # builds bin/tunnel (downloads zenoh-c)
ls -la bin/tunnel

cd bridge/unitree-g1-balance
TUNNEL_BIN=../../bin/tunnel \
PYTHONPATH=$PWD \
LD_LIBRARY_PATH=$PWD/../../.zenoh-c/lib \
UNITREE_G1_PAYMENT_GATE_ZENOH_PORT=7447 \
python tests/test_unitree_g1_payment_gate.py -v
```

Expected output — four scenarios, each exercising the **real Tunnel binary**,
its x402 middleware, a local facilitator, and a Zenoh ActionEvent observer:

1. `test_unpaid_malformed_and_facilitator_rejected_requests_fail_closed` —
   unpaid/malformed → HTTP 402; `isValid:false` (a forged signature) → 402,
   **zero ActionEvents**, zero `/settle` calls.
2. `test_paid_action_publishes_and_settles` — verified payment → 202 →
   ActionEvent → correlated MuJoCo result → state `succeeded`, `settled=True`.
3. `test_failed_execution_does_not_settle` — simulator returns `fall` →
   state `failed`, `settled=False`, zero `/settle` calls.
4. `test_timeout_does_not_settle` — no simulator result → state `timeout`,
   `settled=False`, zero `/settle` calls.

This is the same shape the maintainer probes when sending an `isValid:false`
payment directly at the Tunnel: the gate must fail closed with no ActionEvent.

## 3. Demo (paid flow end to end)

```bash
cd bridge/unitree-g1-balance
python -m flow.demo --all
```

Expected:

```
 skill                        status     settled   pitch(rad)  maxPitch  fell
-----------------------------------------------------------------------------------
 balance_recover {}           completed      True      +0.030       0.216   False
 stop {}                      completed      True      +0.001       0.001   False
 balance_recover {push:8.0}   failed         False      +0.519       0.519   True
===============================================================================
 PASS: every success settles, the genuine fall does not.
```

`pitch` / `maxPitch` are read from the physics solver — no replay. The hard-push
`fall` (torso pitch 0.519 rad > 0.50 rad fall threshold) is a genuine dynamics
outcome.

## 4. Sim-to-sim agreement (Criterion #6)

```bash
cd bridge/unitree-g1-balance
pytest -q tests/test_sim2sim.py
```

Static layers (URDF/joint chain/link offsets/leg axes incl. `torso_pitch`) run
everywhere and pass; the dynamic MuJoCo↔PyBullet layer runs where a real PyBullet
wheel is importable (Linux CI) and is honestly skipped elsewhere — never faked.

## 5. On-chain settlement (Criterion #7)

```bash
python verify_settlement.py
```

Queries Base Sepolia for the transfers and prints the receipt. The evidence file
(`bridge/unitree-g1-balance/docs/evidence/x402-evidence.json`) declares **5 real
settlement txs**, all 0.1 USDC to canonical payee
`0x742d35Cc6634C0532925a3b844Bc454e4438f44e`. Example (verified live):

- txHash: `0xcf0222171e83fd6c0d3981cf202de984c1dd0cb10f06d81eef76da779a5fb6d2`
- block: `45115386` (status Success)
- payer → payee: `0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a` → `0x742d35Cc6634C0532925a3b844Bc454e4438f44e`
- amount: `0.1 USDC`, asset `0x036CbD53842c5426634e7929541eC2318f3dCF7e`

Cross-check on [sepolia.basescan.org](https://sepolia.basescan.org/tx/0xcf0222171e83fd6c0d3981cf202de984c1dd0cb10f06d81eef76da779a5fb6d2).
`accepted_payers` in the evidence file also lists the deployment sub-wallet
`0x2404203a...4f8476B62` that broadcast a subset of these settlements; both
settle to the same canonical payee.

## 6. Profile / manifest contract (Criterion #3)

```bash
cd bridge/unitree-g1-balance
pytest -q tests/test_profiles.py
```

Asserts every number in the five YAML profiles matches `g1_spec.py` and the
transport layer — the documented bridge and the running bridge cannot drift.

---

Runbook generated for RoboPay Tier 1 bounty — laok vendor (balance-recover).
