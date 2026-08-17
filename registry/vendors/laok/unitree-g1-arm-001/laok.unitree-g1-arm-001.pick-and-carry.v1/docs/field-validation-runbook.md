# Field Validation Runbook — unitree-g1 (RoboPay Tier 1)

Step-by-step guide for the maintainer to reproduce every acceptance claim in
this PR on a clean checkout. All commands run from the repository root unless
noted. No secrets are required: payment keys are read from environment
variables and never committed.

## 0. Prerequisites

```bash
# ubuntu-22.04, Python 3.11
pip install -r bridge/unitree-g1/requirements.txt
pip install "x402>=0.2.0" eth-account web3 httpx
```

## 1. Unit tests (Criterion #1/#3/#4/#5/#6)

```bash
cd bridge/unitree-g1
pytest -q
```

Expected: **all tests pass** on the reference platform. The MuJoCo/physics
tests and the sim-to-sim dynamic layer run where a real engine is importable
(Linux CI, or the managed MuJoCo venv); on Windows `pybullet`/`zenoh` have no
wheels so those dynamic layers are honestly skipped (their call paths are still
covered by `tests/bullet_stub.py` / the loopback transport).

## 2. Real Go Tunnel payment gate (Criterion #1/#4)

```bash
make build                          # builds bin/tunnel (downloads zenoh-c)
ls -la bin/tunnel

cd bridge/unitree-g1
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
3. `test_failed_execution_does_not_settle` — simulator returns failure →
   state `failed`, `settled=False`, zero `/settle` calls.
4. `test_timeout_does_not_settle` — no simulator result → state `timeout`,
   `settled=False`, zero `/settle` calls.

This is the same shape the maintainer probes when sending an `isValid:false`
payment directly at the Tunnel: the gate must fail closed with no ActionEvent.

## 3. Demo (paid flow end to end)

```bash
cd bridge/unitree-g1
python -m flow.demo --all
```

Expected:

```
 skill             status      settled   dist(m)   steps
------------------------------------------------------------------------------
 pick_and_carry    completed      True    2.0002     957
 stop              completed      True    0.0002      50
 pick_and_carry {'dropDistance': 8.0}failed        False    2.0884    1000
==============================================================================
 PASS: every success settles, the genuine timeout does not.
```

`dist` and `steps` are read from the physics solver — no replay.

## 4. Sim-to-sim agreement (Criterion #6)

```bash
cd bridge/unitree-g1
pytest -q tests/test_sim2sim.py
```

Static layers (URDF/joint chain/link offsets/leg axes) run everywhere and
pass; the dynamic MuJoCo↔PyBullet layer runs where a real PyBullet wheel is
importable (Linux CI) and is honestly skipped elsewhere — never faked.

## 5. On-chain settlement (Criterion #7)

```bash
python verify_settlement.py
```

Queries Base Sepolia for the transfer and prints the receipt:

- txHash: `0xcb9cab548125ddf34980bf14a5bbb57d8a86d9896348a46d63f9178f34470cc4`
- block: `45415117` (status Success)
- payer → payee: `0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a` → `0x742d35Cc6634C0532925a3b844Bc454e4438f44e`
- amount: `0.1 USDC`, asset `0x036CbD53842c5426634e7929541eC2318f3dCF7e`

Cross-check on [sepolia.basescan.org](https://sepolia.basescan.org/tx/0xcb9cab548125ddf34980bf14a5bbb57d8a86d9896348a46d63f9178f34470cc4).

## 6. Profile / manifest contract (Criterion #3)

```bash
cd bridge/unitree-g1
pytest -q tests/test_profiles.py
```

Asserts every number in the five YAML profiles matches `g1_spec.py` and the
transport layer — the documented bridge and the running bridge cannot drift.

---
Runbook generated for RoboPay Tier 1 bounty — laok vendor.
