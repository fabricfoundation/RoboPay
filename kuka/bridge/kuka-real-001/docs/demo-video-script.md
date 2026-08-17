# Demo Video Script — kuka-real-001 / `pick_object`

**Goal:** a ~4-minute screen recording that proves the Tier 1 "Simulator Skill
Execution" bounty end-to-end: a real physics simulator (MuJoCo) executes a paid
skill, payment is enforced before execution, and **settlement only happens on
success**.

**Recording environment:** a clean terminal on Ubuntu 22.04 (same as CI).
Font large enough to read. Show the command, hit enter, then read the output.

**Local prerequisites (do once, off-camera or in the first 20s):**
```bash
cd bridge/kuka-real-001
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 00:00–00:20 — Title card + context
- **On screen:** `README.md` header, then:
  ```
  RoboPay Tier 1 — Simulator Skill Execution
  kuka-real-001  ·  skill: pick_object  ·  engine: MuJoCo 3.11
  ```
- **Voiceover:** "This is kuka-real-001, a paid robotic-arm skill running inside
  a real physics simulator. It answers the Tier 1 bounty: prove a simulator
  actually executes a paid skill, and that you only get charged when it succeeds."

## 00:20–00:50 — Layout + profiles as runtime contract
- **On screen:** `tree -L 2` (or `ls`), then `cat profiles/skills.yaml` (just the
  `pick_object` pricing + `settlement: on-success-only` block) and
  `cat payment-policy.yaml` (the `safety:` block with every dangerous flag `false`).
- **Voiceover:** "Five YAML profiles aren't documentation — they're the runtime
  contract. The 402 price and the parameter validation both come from these files,
  and a dedicated CI job fails if they ever drift from the code."

## 00:50–01:30 — Single paid run, step by step (`python -m flow.demo`)
- **On screen:** run `python -m flow.demo --object cube`, let it print the 10 steps:
  1. `list_skills` (free) → sees `pick_object: 0.10 USDC`
  2. `request_action` with no payment → **402 Payment Required**
  3. "execution calls before payment: 0" (proves no free execution)
  4. pay (mock envelope)
  5. `submit_paid_action` (six-field envelope)
  6. action published on `robot/tunnel/action`
  7. simulator executes the deterministic trajectory
  8. result on `robot/tunnel/result`
  9. `settled=True`
  10. replay with same idempotency key → **rejected** (no double execution)
- **Voiceover:** "No payment, no execution. After payment, the simulator runs the
  trajectory, lifts the cube, and only then is the payment settled. Replaying the
  same idempotency key is rejected — no double charge."

## 01:30–02:10 — The payment-safety matrix (`python -m flow.demo --all`)
- **On screen:** run `python -m flow.demo --all`, show the summary table:
  ```
   scene        status     reason        lifted(m)  force(N)  steps  settled
   cube         completed  picked           0.1313      9.81    260     True
   unreachable  failed     unreachable     -0.0002      0.00     70    False
   collision    failed     collision       -0.0002      0.00     24    False
   timeout      failed     timeout         -0.0002      0.00     60    False
  ```
- **Voiceover:** "Here's the core invariant. The cube is picked and settled. But a
  target that's unreachable, a path that collides, or a run that times out — all
  fail, and **none of them settle**. You are never charged for a skill that didn't
  succeed. That is criterion #7, proven by the simulator itself."

## 02:10–02:50 — Test suite green
- **On screen:** `python -m pytest -q` → `67 passed, 9 skipped`. Then
  `python -m pytest tests/test_profiles.py -q` → `37 passed`.
- **Voiceover:** "The same assertions run on CI across Python 3.11 and 3.12,
  including the PyBullet Sim-to-Sim and Zenoh transport tests. The profile-parity
  job guarantees the YAML you just saw matches the running bridge."

## 02:50–03:20 — Acceptance mapping
- **On screen:** `cat VALIDATION.md` scrolled to the 13-criterion table.
- **Voiceover:** "Every one of the 13 acceptance criteria maps to a file and a
  test. The README reproduces the whole thing in under five minutes."

## 03:20–04:00 — Close + call to action
- **On screen:** final terminal with the repo path and the PR link placeholder.
- **Voiceover:** "Fork, drop `bridge/kuka-real-001/` into RoboPay, push, and the
  CI proves it. Thanks for reviewing."

---

## Notes for the recorder
- Keep the terminal wide; the summary table is the money shot — pause on it ~5s.
- If MuJoCo ever needs a license prompt, use `export MUJOCO_PLUGIN_DIR=""` (MuJoCo
  3.x is license-free for this model).
- All values above are from a real run on this repo (`python -m flow.demo --all`).
