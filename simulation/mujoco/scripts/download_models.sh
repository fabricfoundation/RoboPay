#!/bin/bash
# Download MuJoCo Menagerie G1 model files.
# Run this once before running the simulation.
#
# Usage: bash download_models.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_DIR="$SCRIPT_DIR/models/unitree_g1"
ASSETS_DIR="$MODEL_DIR/assets"
BASE_URL="https://raw.githubusercontent.com/google-deepmind/mujoco_menagerie/main/unitree_g1"

mkdir -p "$ASSETS_DIR"

echo "Downloading Unitree G1 model from MuJoCo Menagerie..."

# XML files
for f in g1.xml scene.xml; do
    curl -sL "$BASE_URL/$f" -o "$MODEL_DIR/$f"
    echo "  Downloaded $f"
done

# STL meshes
STL_FILES=(
    pelvis.STL pelvis_contour_link.STL
    left_hip_pitch_link.STL left_hip_roll_link.STL left_hip_yaw_link.STL
    left_knee_link.STL left_ankle_pitch_link.STL left_ankle_roll_link.STL
    right_hip_pitch_link.STL right_hip_roll_link.STL right_hip_yaw_link.STL
    right_knee_link.STL right_ankle_pitch_link.STL right_ankle_roll_link.STL
    waist_yaw_link_rev_1_0.STL waist_roll_link_rev_1_0.STL torso_link_rev_1_0.STL
    logo_link.STL head_link.STL
    left_shoulder_pitch_link.STL left_shoulder_roll_link.STL left_shoulder_yaw_link.STL
    left_elbow_link.STL left_wrist_roll_link.STL left_wrist_pitch_link.STL left_wrist_yaw_link.STL
    left_rubber_hand.STL
    right_shoulder_pitch_link.STL right_shoulder_roll_link.STL right_shoulder_yaw_link.STL
    right_elbow_link.STL right_wrist_roll_link.STL right_wrist_pitch_link.STL right_wrist_yaw_link.STL
    right_rubber_hand.STL
)

for f in "${STL_FILES[@]}"; do
    curl -sL "$BASE_URL/assets/$f" -o "$ASSETS_DIR/$f"
done
echo "  Downloaded ${#STL_FILES[@]} STL meshes"

echo "Done! Model files at: $MODEL_DIR"
echo "Verify: python3 -c \"import mujoco; m = mujoco.MjModel.from_xml_path('$MODEL_DIR/scene.xml'); print(f'G1: {m.nq} DOF, {m.nbody} bodies')\""
