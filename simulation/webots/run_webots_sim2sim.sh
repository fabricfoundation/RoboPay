#!/usr/bin/env bash
# Run the Go2 sim-to-sim measurement under the real Webots physics engine.
#
# Usage (from simulation/webots):  bash run_webots_sim2sim.sh
#
# Honesty contract: when the Webots runtime is not available the harness runs
# in SKIP mode and exits 0 WITHOUT producing a measured result (no validation
# is claimed). When Webots is available the harness runs as a real controller
# and writes the measured report; the script exits non-zero on a real FAIL.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WEBOTS_BIN="${WEBOTS_HOME:-/opt/webots}/webots"
if [ ! -x "$WEBOTS_BIN" ]; then
  echo "Webots binary not found at '$WEBOTS_BIN'. Running harness in SKIP mode."
  (cd "$HERE" && python3 test_sim2sim_go2_webots.py)
  exit 0
fi

export WEBOTS_HOME="$(dirname "$WEBOTS_BIN")"
export WEBOTS_PYTHON="${WEBOTS_PYTHON:-$(command -v python3)}"

echo "Fetching Go2 model assets..."
(cd "$HERE/.." && bash setup.sh)

echo "Launching Webots ($WEBOTS_BIN) on go2_sim2sim.wbt..."
cd "$HERE"
exec xvfb-run -a "$WEBOTS_BIN" --batch --mode=fast --minimize \
  --stdout --stderr "$HERE/go2_sim2sim.wbt"
