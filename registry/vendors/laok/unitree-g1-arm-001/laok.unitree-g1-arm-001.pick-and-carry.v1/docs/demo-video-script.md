# Demo Video Script — `unitree-g1` planar biped / paid pick-and-carry skill

**Goal:** a ~4-minute screen recording that proves the Tier 1 "Simulator Skill
Execution" bounty end-to-end: a real physics simulator (MuJoCo) executes a paid
pick-and-carry skill, payment is enforced before execution, and **settlement only
happens on success**.

**Recording environment:** a clean terminal on Ubuntu 22.04 (same as CI).
Font large enough to read. Show the command, hit enter, then read the output.

**Local prerequisites (do once, off-camera or in the first 20s):**
```bash
cd bridge/unitree-g1
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 00:00–00:20 — Title card + context
- **On screen:** `README.md` header, then:
  ```
  RoboPay Tier 1 — Simulator Skill Execution
  unitree-g1  ·  skill: pick_and_carry  ·  engine: MuJoCo 3.11
  planar biped, 4 actuated joints, deterministic gait
  ```
- **Voiceover:** "This is unitree-g1, a paid pick-and-carry skill running inside a
  real physics simulator. It answers the Tier 1 bounty: prove a simulator actually
  executes a paid skill, and that you only get charged when it succeeds."

## 00:20–00:50 — Layout + profiles as runtime contract
- **On screen:** `tree -L 2` (or `ls`), then `cat profiles/skills.yaml` (just the
  `pick_and_carry` pricing + `settlement: on-success-only` block) and
  `cat payment-policy.yaml` (the `safety:` block with every dangerous flag `false`).
- **Voiceover:** "Five YAML profiles aren't documentation — they're the runtime
  contract. The 402 price and the parameter validation both come from these files,
  and a dedicated CI job fails if they ever drift from the code."

## 00:50–01:30 — Single paid run, step by step (`python -m flow.demo`)
- **On screen:** run `python -m flow.demo --skill pick_and_carry`, let it print the 10 steps:
  1. `list_skills` (free) → sees `pick_and_carry: 0.10 USDC`
  2. `request_action` with no payment → **402 Payment Required**
  3. "executions before payment: 0" (proves no free execution)
  4. pay (mock envelope)
  5. `submit_paid_action` (six-field envelope)
  6. action published on `robot/tunnel/action`
  7. simulator executes the deterministic gait, acquires the object, carries it
  8. result on `robot/tunnel/result`
  9. `settled=True`
  10. replay with same idempotency key → **rejected** (no double execution)
- **Voiceover:** "No payment, no execution. After payment, the simulator runs the
  gait, advances the torso ~2.0 m while carrying the object, and only then is the
  payment settled. Replaying the same idempotency key is rejected — no double charge."

## 01:30–02:10 — The payment-safety matrix (`python -m flow.demo --all`)
- **On screen:** run `python -m flow.demo --all`, show the summary table:
  ```
   scene                   status     reason       dist(m)  steps  settled
  ------------------------------------------------------------------------------
   pick_and_carry            completed  carried        2.0002    957     True
   stop                    completed  stopped       0.0002     50     True
   pick_and_carry(timeout)   failed     timeout       2.0884   1000    False
  ==============================================================================
   PASS: success settles, the timeout failure does not.
  ```
- **Voiceover:** "Here's the core invariant. pick_and_carry and stop both succeed
  and settle. But the timeout row — a drop distance of 8.0 m that is valid per the
  schema yet larger than any gait budget can reach — runs the real physics to
  exhaustion, fails, and **does not settle**. You are never charged for a skill
  that didn't succeed. That is criterion #7, proven by the simulator itself."

## 02:10–02:50 — Test suite green
- **On screen:** `python -m pytest -q` → all pass. Then
  `python -m pytest tests/test_sim2sim.py -q` → sim-to-sim agreement.
- **Voiceover:** "The same assertions run on CI across Python 3.10 and 3.11,
  including the PyBullet Sim-to-Sim and Zenoh transport tests. The profile-parity
  job guarantees the YAML you just saw matches the running bridge."

## 02:50–03:20 — Acceptance mapping
- **On screen:** `cat docs/validation-report.md` scrolled to the criterion table.
- **Voiceover:** "Every acceptance criterion maps to a file and a test. The real
  on-chain settlement is verifiable on Base Sepolia — the report links the txHash."

## 03:20–04:00 — Close + call to action
- **On screen:** final terminal with the repo path and the PR link placeholder.
- **Voiceover:** "Drop `bridge/unitree-g1/` into RoboPay, push, and the CI proves
  it. Thanks for reviewing."

---

## Notes for the recorder
- Keep the terminal wide; the summary table is the money shot — pause on it ~5s.
- If MuJoCo ever needs a license prompt, use `export MUJOCO_PLUGIN_DIR=""` (MuJoCo
  3.x is license-free for this model).
- All values above are from a real run on this repo (`python -m flow.demo --all`,
  MuJoCo 3.11, single thread) and are deterministic.
