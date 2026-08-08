# Reviewer video runbook

Target length: 2-4 minutes. Record one continuous take. Never display the
payer private key, shell history containing it, a seed phrase, or local config
contents.

## Before recording

Open Ubuntu/WSL and load the payer key while recording is **off**:

```bash
read -rsp "Enter payer private key (hidden): " X402_PRIVATE_KEY
echo
export X402_PRIVATE_KEY
clear
```

Use a new fixed key for this video:

```bash
export VIDEO_KEY="x2-video-$(date +%s)"
echo "$VIDEO_KEY"
```

Do not recreate `config.local.json` on camera. It is intentionally ignored.

Start the verified bridge+tunnel stack with one command:

```bash
bash /mnt/c/Users/yezir/Documents/Codex/2026-07-22/ta/RoboPay/bridge/agibot/x2/mujoco_bridge/test/start_video_stack.sh
```

Do not start recording unless it prints:

```text
READY: bridge PID ..., tunnel HTTP 402
```

## Record the proof

Start recording now. First show the branch and commit:

```bash
cd /mnt/c/Users/yezir/Documents/Codex/2026-07-22/ta/RoboPay
git status -sb
git log -1 --oneline
```

Show the unsigned payment challenge:

```bash
curl -sS -D - -o /dev/null -X POST http://127.0.0.1:3000/action \
  -H 'Content-Type: application/json' --data '{}'
```

Show balances on BaseScan, then perform one paid execution:

```bash
/tmp/robopay-paid-client \
  -payer 0xe09729896fa906c336b2Ed36a7A08BB19E5De194 \
  -url http://127.0.0.1:3000/action \
  -action move_forward -duration 1 \
  -idempotency-key "$VIDEO_KEY"
```

Point out HTTP `200`, `SUCCESS`, `state_delta`, `root_displacement`, and the
transaction in `PAYMENT-RESPONSE`. Show the matching bridge execution log:

```bash
tail -20 ~/robopay-work/evidence/video-bridge.log
```

Replay the same key:

```bash
/tmp/robopay-paid-client \
  -payer 0xe09729896fa906c336b2Ed36a7A08BB19E5De194 \
  -url http://127.0.0.1:3000/action \
  -action move_forward -duration 1 \
  -idempotency-key "$VIDEO_KEY" || true
```

Point out HTTP `409`, `REPLAY_REJECTED`, the absent `PAYMENT-RESPONSE`, and the
absence of a second `Executing policy` log. Refresh BaseScan and show that only
the successful request transferred `0.002 USDC`.

## Cleanup after recording

```bash
unset X402_PRIVATE_KEY VIDEO_KEY
docker stop robopay-x2-video 2>/dev/null || true
kill "$(cat ~/robopay-work/evidence/video-bridge.pid)" 2>/dev/null || true
```

Review the recording before upload and verify that no secret, seed phrase,
wallet popup, environment dump, or local config appears.
