#!/bin/sh
# Fetch the official MuJoCo model assets used by the simulators in this repo
# (google-deepmind/mujoco_menagerie, BSD-3-Clause; requires MuJoCo >= 3.1.3):
#
#   - boston_dynamics_spot -> simulation/spot  + PyBullet sim-to-sim
#   - unitree_go2          -> simulation/go2   + PyBullet sim-to-sim
#
# Idempotent and safe to re-run: both robots share one sparse menagerie clone,
# and an existing checkout is extended in place rather than re-cloned.
set -e
cd "$(dirname "$0")"

MENAGERIE_COMMIT=da76818e269b82289eba39808e2fb91d679d6994
# Override with GIT_HOST=git@github.com: for SSH-only environments
GIT_HOST="${GIT_HOST:-https://github.com/}"

sparse_clone() {
    repo="$1"; dest="$2"; commit="$3"; path="$4"
    if [ -e "$dest/$path" ]; then
        echo "$dest/$path already set up"
        return
    fi
    if [ -d "$dest/.git" ]; then
        git -C "$dest" sparse-checkout add "$path"
        git -C "$dest" checkout --quiet "$commit"
        echo "$dest: sparse set extended to $path"
    else
        git clone --filter=blob:none --sparse "${GIT_HOST}${repo}.git" "$dest"
        git -C "$dest" sparse-checkout set "$path"
        git -C "$dest" checkout --quiet "$commit"
    fi
}

mkdir -p models
sparse_clone google-deepmind/mujoco_menagerie \
    models/mujoco_menagerie "$MENAGERIE_COMMIT" boston_dynamics_spot
sparse_clone google-deepmind/mujoco_menagerie \
    models/mujoco_menagerie "$MENAGERIE_COMMIT" unitree_go2

echo "OK: models ready (menagerie boston_dynamics_spot + unitree_go2)"
