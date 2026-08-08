#!/usr/bin/env bash
set -euo pipefail

log_file="${TMPDIR:-/tmp}/agibot_x2_bridge.log"
ros2 launch mujoco_bridge_agibot_x2 bridge.launch.py >"$log_file" 2>&1 &
bridge_pid=$!
cleanup() {
  kill "$bridge_pid" 2>/dev/null || true
  wait "$bridge_pid" 2>/dev/null || true
}
trap cleanup EXIT

sleep 3
python "$(dirname "$0")/e2e_zenoh.py"
cat "$log_file"
