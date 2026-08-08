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

Prepare three Ubuntu terminals. Use a new fixed key for this video, for example:

```bash
export VIDEO_KEY="x2-video-$(date +%s)"
echo "$VIDEO_KEY"
```

Do not recreate `config.local.json` on camera. It is intentionally ignored.

## Terminal 1: bridge

```bash
source /opt/ros/jazzy/setup.bash
source ~/robopay-work/ros_ws/install/setup.bash
ros2 launch mujoco_bridge_agibot_x2 bridge.launch.py \
  zenoh_listen:=tcp/0.0.0.0:7447
```

## Terminal 2: tunnel

```bash
WSL_IP=$(hostname -I | awk '{print $1}')
docker run --rm --name robopay-x2-video -p 3000:3000 \
  -e LOCAL_HTTP_ADDR=:3000 \
  -e ZENOH_CONNECT_ENDPOINT="tcp/${WSL_IP}:7447" \
  -e FACILITATOR_URL=https://facilitator.xpay.sh \
  -v /mnt/c/Users/yezir/Documents/Codex/2026-07-22/ta/RoboPay/tunnel/config.local.json:/app/config.local.json:ro \
  robopay-tunnel:execution-gated -config /app/config.local.json
```

## Terminal 3: record the proof

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
transaction in `PAYMENT-RESPONSE`. Show Terminal 1's matching execution log.

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
```

Stop the bridge with Ctrl+C. Review the recording before upload and verify that
no secret, seed phrase, wallet popup, environment dump, or local config appears.
