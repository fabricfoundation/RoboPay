# Validation -- deep-robotics-m20-pro

| Criterion | How it is met |
|---|---|
| 1 invalid fail-closed | `test_invalid_fail_closed.py`: no payment -> 402; invalid skill -> rejected, never settled |
| 2 actionId -> terminal result | `TaskEnvelope.actionId` carried through relay -> result; `test_transport.py` |
| 3 settle on success only | `test_payment_gate.py`: success settles, failure does not |
| 4 no settle on failure/timeout/replay | `test_payment_gate.py` + `test_x402.py` replay test |
| 5 bounded + interruptible | `test_safe_stop.py`: stop terminates within budget |
| 6 reproducible HEAD CI | `pytest` green on HEAD; sim2sim test (skipped on Windows) |
| 7 independent on-chain receipt | `docs/evidence/x402-evidence.json` real Base Sepolia tx; `pay.py` mints it |
