#!/bin/sh
# One-command verification of the Unitree Go2 tier-1 simulation stack.
#
# Runs every committed acceptance test against the MuJoCo simulator exactly as
# CI does (mujoco-pybullet job), prints PASS/FAIL per test and exits nonzero
# if any test fails. The tunnel E2E and Webots runs are NOT included here:
# they need the compiled Go tunnel binary and the Webots runtime respectively
# (they are exercised as best-effort CI jobs instead, see
# .github/workflows/go2-simulation-tests.yml).
#
# Usage:
#   bash verify_go2_tier1.sh
set -e
cd "$(dirname "$0")"

if [ ! -e models/mujoco_menagerie/unitree_go2/scene.xml ]; then
    echo "==> fetching model assets"
    bash setup.sh
fi

cd go2
FAIL=0
for t in \
    test_go2_control.py \
    test_payment_gate.py \
    test_result_semantics.py \
    test_link.py \
    test_obstacle_nav.py \
    test_adversarial_nav.py \
    test_durable_replay.py \
    test_settlement.py; do
    echo ""
    echo "========== $t =========="
    if python3 "$t"; then
        echo "$t: PASS"
    else
        echo "$t: FAIL"
        FAIL=1
    fi
done

echo ""
echo "========== pybullet sim-to-sim =========="
cd ../pybullet
if python3 test_sim2sim_go2.py; then
    echo "test_sim2sim_go2.py: PASS"
else
    echo "test_sim2sim_go2.py: FAIL"
    FAIL=1
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "verify_go2_tier1: ALL PASS"
    exit 0
fi
echo "verify_go2_tier1: FAILURES PRESENT"
exit 1
