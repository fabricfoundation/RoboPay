#!/bin/sh
# Fetch the model assets used by the Go2 simulators:
#   - MuJoCo model:  google-deepmind/mujoco_menagerie unitree_go2
#                    (BSD-3-Clause; requires MuJoCo >= 3.1.3)
#   - PyBullet: the sim-to-sim check loads the SAME go2.xml via
#     pybullet.loadMJCF, so no separate URDF is needed (unlike the Spot
#     branch which committed a stripped kinematic URDF).
set -e
cd "$(dirname "$0")"

MENAGERIE_COMMIT=da76818e269b82289eba39808e2fb91d679d6994
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

echo "OK: models ready (menagerie unitree_go2)"
