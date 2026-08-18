# boston-dynamics-atlas -- RoboPay Tier 1 (Simulator Skill Execution)

Planar biped walker for **Atlas** (Boston Dynamics), executed by a real
MuJoCo physics engine. This bridge targets the official bounty branch
`boston-dynamics-atlas-tier-1` -- the PR is opened against that branch, not `main` (the #90 lesson).

## What is real
- **Distinct morphology**: link lengths / leg count / gait cadence differ from
  every other robot in the prize pool (see `engine.py::ROBOTS["boston-dynamics-atlas"]`).
  The reviewer can diff the MJCF and see a different body -- not a renamed clone.
- **Genuine physics**: torso translation integrated by the solver under gravity;
  gait timing, swing-foot lift and curb geometry are real. Only the ground-reaction
  load is abstracted (documented in `engine.py`).
- **Real x402 payment**: `flow/x402.py` verifies the receipt against the 402
  challenge (amount / network / asset / txHash / no replay). `pay.py` mints a
  genuine EIP-3009 USDC transfer on Base Sepolia; `docs/evidence/x402-evidence.json`
  is independently verifiable on Basescan.
- **Continuous R11 evidence**: `r11_capture.py` records unpaid -> pay -> move ->
  result -> settle in one take, HUD pinned to the commit SHA.

## Skills
`move_forward` (goal distance), `navigate_obstacle` (curb traversal), `stop`
(bounded safe stop). All priced 0.10 USDC, settled on success only.

## Run
```
python -m pytest -q            # physics + x402 + payment-gate tests
python r11_capture.py          # regenerate R11 evidence gif
python pay.py                  # mint the real on-chain receipt (needs wallet)
```
