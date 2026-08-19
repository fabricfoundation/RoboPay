#!/bin/sh
# Fetch the official Unitree Go2 model assets used by both simulators:
#   - MuJoCo model:  google-deepmind/mujoco_menagerie (BSD-3-Clause)
#   - Webots meshes: unitreerobotics/unitree_ros go2_description (BSD-3-Clause)
# Both are pinned to the commits this work was validated against.
set -e
cd "$(dirname "$0")"

MENAGERIE_COMMIT=71f066ad0be9cd271f7ed58c030243ef157af9f4
UNITREE_ROS_COMMIT=278b222a3ca04f684c764c53ae82a70c87ff3044
# Override with GIT_HOST=git@github.com: for SSH-only environments
GIT_HOST="${GIT_HOST:-https://github.com/}"

sparse_clone() {
    repo="$1"; dest="$2"; commit="$3"; path="$4"
    if [ -e "$dest/$path" ]; then
        echo "$dest already set up"
        return
    fi
    git clone --filter=blob:none --sparse "${GIT_HOST}${repo}.git" "$dest"
    git -C "$dest" sparse-checkout set "$path"
    git -C "$dest" checkout --quiet "$commit"
}

mkdir -p models
sparse_clone google-deepmind/mujoco_menagerie \
    models/mujoco_menagerie "$MENAGERIE_COMMIT" unitree_go2
sparse_clone unitreerobotics/unitree_ros \
    models/unitree_ros "$UNITREE_ROS_COMMIT" robots/go2_description

mkdir -p webots/protos/go2_meshes
cp models/unitree_ros/robots/go2_description/dae/*.dae webots/protos/go2_meshes/

echo "OK: models ready (menagerie go2 + webots meshes)"
