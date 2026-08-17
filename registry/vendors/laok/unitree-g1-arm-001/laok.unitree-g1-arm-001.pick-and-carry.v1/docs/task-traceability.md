# Task Traceability - unitree-g1

Maps every test and evidence artifact in this PR to the RoboPay Tier 1
integration gate criteria published by @Junzhe.

## Criteria Checklist

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | x402 verification **fails closed** before action dispatch | PASS | `test_unitree_g1_payment_gate.py` |
| 2 | Verified actions **correlated** through simulator result path | PASS | `test_flow.py` / `test_simulator.py` |
| 3 | Settlement occurs **only after** successful execution | PASS | `test_profiles.py` / `test_bridge.py` |
| 4 | Failure / timeout / replay paths **do not settle** | PASS | `test_x402_no_settlement.py` / `test_unitree_g1_payment_gate.py` |
| 5 | Bounded policy + interruptible execution + **safe stop** | PASS | `test_safe_stop.py` |
| 6 | MuJoCo/PyBullet results covered by reproducible **current-head CI** | PASS | `unitree-g1-bridge.yml` |
| 7 | Base Sepolia receipt **independently checked** | PASS | `x402-evidence.json` + `validation-report.md` |

## Test to Criterion Mapping

| Test File | Covers | Description |
|-----------|--------|-------------|
| `test_unitree_g1_payment_gate.py` | #1, #4 | Real Go Tunnel integration: unpaid/malformed/isValid:false -> 402 zero ActionEvents; verified payment -> 202 -> ActionEvent -> correlated result -> settle; failure/timeout never settle |
| `test_safe_stop.py` | #5 | Real MuJoCo safe-stop tests: timeout stops on budget, stop completes in budget, normal scene completes in budget, obstacle scene completes |
| `test_flow.py` | #2 | Action dispatch, result correlation, actionId flow |
| `test_simulator.py` | #2 | MuJoCo simulation, joint trajectory validation |
| `test_sim2sim.py` | #2, #6 | MuJoCo to PyBullet parity, tolerance verification |
| `test_profiles.py` | #3 | Settlement trigger on SUCCESS, no settlement on FAILURE |
| `test_bridge.py` | #3, #4 | Bridge validation, Zenoh message routing, settlement routing |
| `test_x402_no_settlement.py` | #4 | Failure/timeout/replay three-path zero-settlement proof |
| `unitree-g1-bridge.yml` | #6 | Full CI pipeline: lint + test + tunnel-integration + sim2sim + evidence |
| `x402-evidence.json` | #7 | 1 real Base Sepolia Transfer event, payer 0xf274 |

## Chain of Evidence

1. PR head commit -> CI workflow triggers (action_required -> maintainer approve)
2. CI runs: `pytest tests/` + `python tests/test_unitree_g1_payment_gate.py -v`
3. `verify_settlement.py` queries Base Sepolia -> finds Transfer event with topics[1]==0xf274
4. `x402-evidence.json` records the txHash with block number + basescan link
5. `validation-report.md` cross-references test results with on-chain data
6. `settle.png` shows payer=0xf274 in terminal output
7. `task-traceability.md` documents test-to-criterion mapping (this file)

All evidence files are deterministic: re-running the same commit reproduces the
same test outputs and references the same on-chain transactions.

## On-Chain Settlement Verification

- Payer: `0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a`
- Payee: `0x742d35Cc6634C0532925a3b844Bc454e4438f44e`
- Network: Base Sepolia (testnet)
- Token: USDC
- txHash: `0xcb9cab548125ddf34980bf14a5bbb57d8a86d9896348a46d63f9178f34470cc4`
- Block: `45415117` (status Success)
- Verification script: `verify_settlement.py`

---
Generated for RoboPay Tier 1 bounty - laok vendor.
