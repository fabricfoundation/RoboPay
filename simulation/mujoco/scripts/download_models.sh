#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$SCRIPT_DIR/models/boston_dynamics_spot"
ASSETS_DIR="$MODEL_DIR/assets"
BASE_URL="https://raw.githubusercontent.com/google-deepmind/mujoco_menagerie/main/boston_dynamics_spot"
mkdir -p "$ASSETS_DIR"
echo "Downloading spot model from MuJoCo Menagerie..."
for f in $(curl -s "https://api.github.com/repos/google-deepmind/mujoco_menagerie/contents/boston_dynamics_spot" | python3 -c "import sys,json; [print(x['name']) for x in json.load(sys.stdin) if x['name'].endswith('.xml')]"); do
    curl -sL "$BASE_URL/$f" -o "$MODEL_DIR/$f"
    echo "  Downloaded $f"
done
for f in $(grep -oP 'file="([^"]+)"' "$MODEL_DIR"/*.xml 2>/dev/null | grep -oP '"[^"]+"' | tr -d '"' | sort -u | grep -v '.xml'); do
    curl -sL "$BASE_URL/assets/$f" -o "$ASSETS_DIR/$f" 2>/dev/null || true
done
echo "Done! Model at: $MODEL_DIR"
