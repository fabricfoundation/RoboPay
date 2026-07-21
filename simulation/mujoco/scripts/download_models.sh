#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$SCRIPT_DIR/models/unitree_go2"
ASSETS_DIR="$MODEL_DIR/assets"
BASE_URL="https://raw.githubusercontent.com/google-deepmind/mujoco_menagerie/main/unitree_go2"
mkdir -p "$ASSETS_DIR"
echo "Downloading go2 model from MuJoCo Menagerie..."
for f in $(curl -s "https://api.github.com/repos/google-deepmind/mujoco_menagerie/contents/unitree_go2" | python3 -c "import sys,json; [print(x['name']) for x in json.load(sys.stdin) if x['name'].endswith('.xml')]"); do
    curl -sL "$BASE_URL/$f" -o "$MODEL_DIR/$f"
    echo "  Downloaded $f"
done
for f in $(grep -oP 'file="([^"]+)"' "$MODEL_DIR"/*.xml 2>/dev/null | grep -oP '"[^"]+"' | tr -d '"' | sort -u | grep -v '.xml'); do
    curl -sL "$BASE_URL/assets/$f" -o "$ASSETS_DIR/$f" 2>/dev/null || true
done
echo "Done! Model at: $MODEL_DIR"
