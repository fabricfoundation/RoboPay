#!/usr/bin/env bash
set -euo pipefail

repo_root="${ROBOPAY_REPO:-/mnt/c/Users/yezir/Documents/Codex/2026-07-22/ta/RoboPay}"
ros_ws="${ROBOPAY_ROS_WS:-$HOME/robopay-work/ros_ws}"
ros_python="${ROBOPAY_ROS_PYTHON:-$HOME/robopay-work/ros_venv/bin/python}"
log_dir="${ROBOPAY_EVIDENCE_DIR:-$HOME/robopay-work/evidence}"
container="robopay-x2-video"

mkdir -p "$log_dir"
docker rm -f "$container" >/dev/null 2>&1 || true

model="$ros_ws/install/mujoco_bridge_agibot_x2/share/mujoco_bridge_agibot_x2/models/x2_headless.xml"
nohup bash -lc "source /opt/ros/jazzy/setup.bash; source '$ros_ws/install/setup.bash'; exec '$ros_python' -c 'from x2.node import main; main()' --ros-args --params-file '$ros_ws/install/mujoco_bridge_agibot_x2/share/mujoco_bridge_agibot_x2/config/params.yaml' -p zenoh_listen:=tcp/0.0.0.0:7447 -p model_path:='$model'" \
  >"$log_dir/video-bridge.log" 2>&1 </dev/null &
bridge_pid=$!
echo "$bridge_pid" >"$log_dir/video-bridge.pid"

sleep 5
if ! kill -0 "$bridge_pid" 2>/dev/null; then
  echo "Bridge failed to start:" >&2
  tail -50 "$log_dir/video-bridge.log" >&2
  exit 1
fi

wsl_ip="$(hostname -I | awk '{print $1}')"
docker run --rm -d --name "$container" -p 3000:3000 \
  -e LOCAL_HTTP_ADDR=:3000 \
  -e "ZENOH_CONNECT_ENDPOINT=tcp/${wsl_ip}:7447" \
  -e FACILITATOR_URL=https://facilitator.xpay.sh \
  -v "$repo_root/tunnel/config.local.json:/app/config.local.json:ro" \
  robopay-tunnel:execution-gated -config /app/config.local.json >/dev/null

sleep 5
status="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  http://127.0.0.1:3000/action -H 'Content-Type: application/json' --data '{}')"
if [[ "$status" != "402" ]]; then
  echo "Expected HTTP 402, received $status" >&2
  exit 1
fi

echo "READY: bridge PID $bridge_pid, tunnel HTTP 402"
echo "Bridge log: $log_dir/video-bridge.log"
echo "Tunnel log: docker logs -f $container"
